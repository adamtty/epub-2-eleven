"""Local page for previewing cuts and writing the cleaned EPUB."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import webbrowser
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

from epubclean.classify import classify_book
from epubclean.export import destination_for, export_epub
from epubclean.model import Book, EpubError
from epubclean.parse import parse_epub
from epubclean.render import book_view, check_preset, parse_overrides, spoken_document

MAX_UPLOAD = 200 * 1024 * 1024
MAX_JSON = 1024 * 1024
MAX_BOOKS = 8
WEB_DIR = Path(__file__).resolve().parent / "web"
# A fresh secret each start. The page gets it from index.html; every POST must send it back.
TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-Epub-2-Eleven-Token"  # noqa: S105 (a header name, not a secret)
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'; "
    "frame-ancestors 'none'"
)
STATIC = {
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}

_LOCK = threading.Lock()
_BOOKS: OrderedDict[str, Book] = OrderedDict()
_UPLOADS = Path(tempfile.mkdtemp(prefix="epub-2-eleven-"))
atexit.register(shutil.rmtree, _UPLOADS, ignore_errors=True)


def open_book(path: Path, *, from_upload: bool = False, export_stem: str | None = None) -> dict:
    try:
        book = parse_epub(path)
        classify_book(book)
    except RecursionError as exc:
        raise EpubError("This EPUB is nested too deeply to read.") from exc
    book.from_upload = from_upload
    book.export_stem = export_stem or path.stem
    book_id = uuid4().hex
    with _LOCK:
        _BOOKS[book_id] = book
        while len(_BOOKS) > MAX_BOOKS:
            _BOOKS.popitem(last=False)
    payload = book_view(book)
    payload["book_id"] = book_id
    return payload


def store_upload(filename: str, data: bytes) -> dict:
    """Parse an uploaded book from a private temp file, then delete the file."""
    stem = _safe_stem(filename)
    dest = _UPLOADS / f"{uuid4().hex}.epub"
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return open_book(dest, from_upload=True, export_stem=stem)
    finally:
        dest.unlink(missing_ok=True)


def spoken_for(book_id: str, preset: str, overrides: dict[str, bool]) -> dict:
    book = _book(book_id)
    text, counts = spoken_document(book, preset, overrides)
    return {"text": text, "chars": len(text), "chapters": counts}


def export_for(book_id: str, preset: str, overrides: dict[str, bool]) -> dict:
    book = _book(book_id)
    dest = destination_for(book)
    export_epub(book, dest, preset, overrides)
    text, _counts = spoken_document(book, preset, overrides)
    return {"path": str(dest), "chars": len(text)}


def pick_file() -> dict | None:
    if sys.platform != "darwin":
        raise EpubError(
            "The Open button's file dialog is on macOS. Drop the EPUB onto the page instead."
        )
    # A fixed script with no user input, run by absolute path with a bare environment.
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", 'POSIX path of (choose file with prompt "Open an EPUB")'],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        check=False,
    )
    if result.returncode != 0:
        if "User canceled" in result.stderr or result.returncode == 1:
            return None
        raise EpubError("The file dialog could not be opened.")
    chosen = result.stdout.strip()
    if not chosen:
        return None
    return open_book(Path(chosen))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Clean an EPUB so ElevenLabs reads the book.")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"epub-2-eleven is running at {url}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if not self._host_ok():
            self._json({"error": "Request blocked."}, status=403)
            return
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
            self._send(html.replace("__TOKEN__", TOKEN).encode(), "text/html; charset=utf-8")
            return
        if path in STATIC:
            name, content_type = STATIC[path]
            self._send((WEB_DIR / name).read_bytes(), content_type)
            return
        self._json({"error": "Not found."}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            # Checked before the body is read, so a page that can't pass them can't make us read it.
            if not self._post_ok():
                self._json({"error": "Request blocked."}, status=403)
                return
            if path == "/api/pick":
                self._read_json()
                opened = pick_file()
                self._json({"cancelled": True} if opened is None else opened)
                return
            if path == "/api/upload":
                self._json(self._upload())
                return
            data = self._read_json()
            if path == "/api/spoken":
                book_id, preset, overrides = _request_book(data)
                self._json(spoken_for(book_id, preset, overrides))
                return
            if path == "/api/export":
                book_id, preset, overrides = _request_book(data)
                self._json(export_for(book_id, preset, overrides))
                return
            self._json({"error": "Not found."}, status=404)
        except EpubError as exc:
            self._json({"error": str(exc)}, status=400)
        except Exception:
            traceback.print_exc()
            self._json({"error": "Something went wrong reading that file."}, status=500)

    def _hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _host_ok(self) -> bool:
        # DNS-rebinding guard: only answer to our own loopback names.
        return (self.headers.get("Host") or "").lower() in self._hosts()

    def _post_ok(self) -> bool:
        if not self._host_ok():
            return False
        if not secrets.compare_digest(self.headers.get(TOKEN_HEADER) or "", TOKEN):
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in {f"http://{h}" for h in self._hosts()}:
            return False
        site = self.headers.get("Sec-Fetch-Site")
        return site is None or site in {"same-origin", "none"}

    def _length(self, limit: int) -> int:
        raw = self.headers.get("Content-Length") or "0"
        if not raw.isdigit():
            raise EpubError("The request had no valid length.")
        length = int(raw)
        if length > limit:
            raise EpubError("That request is too large.")
        return length

    def _upload(self) -> dict:
        length = self._length(MAX_UPLOAD)
        if length == 0:
            raise EpubError("That file is empty.")
        filename = unquote(self.headers.get("X-Filename") or "") or "book.epub"
        if not filename.lower().endswith(".epub"):
            raise EpubError("Choose an .epub file.")
        return store_upload(filename, self.rfile.read(length))

    def _read_json(self) -> dict:
        length = self._length(MAX_JSON)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
            raise EpubError("The request was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise EpubError("Expected a JSON object.")
        return data

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8", status)

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(body)


def _book(book_id: str) -> Book:
    with _LOCK:
        book = _BOOKS.get(book_id)
    if book is None:
        raise EpubError("Open an EPUB first.")
    return book


def _request_book(data: dict) -> tuple[str, str, dict[str, bool]]:
    book_id = str(data.get("book_id") or "")
    preset = check_preset(str(data.get("preset") or "narration"))
    overrides = parse_overrides(data.get("overrides") or {})
    _book(book_id)
    return book_id, preset, overrides


def _safe_stem(filename: str) -> str:
    # Not Path.stem: how it treats names like "...epub" changed in Python 3.15.
    stem = Path(filename).name
    if stem.lower().endswith(".epub"):
        stem = stem[: -len(".epub")]
    stem = re.sub(r"[^\w.\- ]+", "", stem, flags=re.UNICODE).strip(" .")
    return stem[:120] or "book"


if __name__ == "__main__":
    main()
