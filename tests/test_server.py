import http.client
import json
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from epubclean import server
from epubclean.server import TOKEN, TOKEN_HEADER, Handler
from tests.epubs import semantic_book


@pytest.fixture
def port(tmp_path, monkeypatch):
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def request(port, method, path, body=b"", headers=None, *, token=True, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{port}")
    sent = {"Content-Length": str(len(body))}
    if token:
        sent[TOKEN_HEADER] = TOKEN
    sent.update(headers or {})
    for key, value in sent.items():
        conn.putheader(key, value)
    conn.endheaders()
    if body:
        conn.send(body)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, dict(response.getheaders()), data


def post_json(port, path, payload, **kwargs):
    status, headers, data = request(
        port, "POST", path, json.dumps(payload).encode(), {"Content-Type": "application/json"},
        **kwargs,
    )  # fmt: skip
    return status, json.loads(data)


def upload(port, source: Path, name: str | None = None, **kwargs):
    status, _headers, data = request(
        port,
        "POST",
        "/api/upload",
        source.read_bytes(),
        {"Content-Type": "application/epub+zip", "X-Filename": name or source.name},
        **kwargs,
    )
    return status, json.loads(data)


def test_upload_and_export_round_trip(port, tmp_path):
    source = semantic_book(tmp_path / "Sample.epub")
    status, opened = upload(port, source)
    assert status == 200
    assert opened["title"] == "Sample"
    assert opened["source_name"] == "Sample.epub"  # not the temp file's random name
    assert opened["spoken"]["chars"] > 0
    assert any(chapter["role"] == "body" for chapter in opened["chapters"])

    status, _headers, html = request(port, "GET", "/")
    assert status == 200
    assert "What the voice reads" in html.decode()

    status, exported = post_json(
        port,
        "/api/export",
        {"book_id": opened["book_id"], "preset": "narration", "overrides": {}},
    )
    assert status == 200
    dest = Path(exported["path"])
    assert dest == tmp_path / "home" / "Downloads" / "Sample-elevenlabs.epub"
    assert dest.is_file()
    assert exported["chars"] == opened["spoken"]["chars"]


def test_upload_is_deleted_after_parsing(port, tmp_path):
    source = semantic_book(tmp_path / "Sample.epub")
    assert upload(port, source)[0] == 200
    assert list(server._UPLOADS.iterdir()) == []


def test_unicode_filename_is_percent_encoded(port, tmp_path):
    source = semantic_book(tmp_path / "Sample.epub")
    status, opened = upload(port, source, name="%E6%9C%AC%20book.epub")
    assert status == 200
    status, exported = post_json(port, "/api/export", {"book_id": opened["book_id"]})
    assert exported["path"].endswith("本 book-elevenlabs.epub")


def test_page_carries_the_token_and_a_strict_csp(port):
    status, headers, html = request(port, "GET", "/", token=False)
    assert status == 200
    assert f'content="{TOKEN}"' in html.decode()
    csp = headers["Content-Security-Policy"]
    for rule in ("default-src 'none'", "script-src 'self'", "base-uri 'none'", "object-src 'none'"):
        assert rule in csp
    assert "unsafe-inline" not in csp
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize("host", ["evil.example", "evil.example:{port}", "127.0.0.1:1"])
def test_foreign_host_is_refused(port, host):
    # DNS rebinding: a page on evil.example resolved to 127.0.0.1
    status, _headers, data = request(port, "GET", "/", host=host.format(port=port))
    assert status == 403
    assert TOKEN.encode() not in data
    status, _body = post_json(port, "/api/spoken", {}, host=host.format(port=port))
    assert status == 403


def test_localhost_name_is_accepted(port):
    assert request(port, "GET", "/", host=f"localhost:{port}")[0] == 200


@pytest.mark.parametrize(
    "headers",
    [
        {TOKEN_HEADER: ""},
        {TOKEN_HEADER: "wrong"},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_cross_site_posts_are_refused(port, tmp_path, headers):
    source = semantic_book(tmp_path / "Sample.epub")
    status, _headers, _data = request(
        port, "POST", "/api/upload", source.read_bytes(), {"X-Filename": "a.epub", **headers}
    )
    assert status == 403


def test_missing_token_is_refused(port, tmp_path):
    source = semantic_book(tmp_path / "Sample.epub")
    assert upload(port, source, token=False)[0] == 403


def test_same_origin_post_is_accepted(port, tmp_path):
    source = semantic_book(tmp_path / "Sample.epub")
    headers = {"Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
    status, _headers, _data = request(
        port, "POST", "/api/upload", source.read_bytes(), {"X-Filename": "a.epub", **headers}
    )
    assert status == 200


def test_oversized_request_is_refused_without_reading_it(port):
    # Claims more than the cap and sends nothing: the server must answer, not wait for the body.
    status, _headers, data = request(
        port, "POST", "/api/spoken", b"", {"Content-Length": str(server.MAX_JSON + 1)}
    )
    assert status == 400
    assert b"too large" in data


@pytest.mark.parametrize("length", ["-1", "abc", "1e9"])
def test_bad_content_length_is_a_clean_error(port, length):
    status, _headers, _data = request(port, "POST", "/api/spoken", b"", {"Content-Length": length})
    assert status == 400


def test_open_by_path_is_gone(port, tmp_path):
    source = semantic_book(tmp_path / "Sample.epub")
    status, _body = post_json(port, "/api/open", {"path": str(source)})
    assert status == 404


def test_deep_json_is_a_clean_error(port):
    body = b"[" * 100_000 + b"]" * 100_000
    status, _headers, _data = request(port, "POST", "/api/spoken", body)
    assert status == 400


def test_only_recent_books_are_kept(port, tmp_path):
    source = semantic_book(tmp_path / "Sample.epub")
    ids = [upload(port, source)[1]["book_id"] for _ in range(server.MAX_BOOKS + 1)]
    assert post_json(port, "/api/spoken", {"book_id": ids[0]})[0] == 400
    assert post_json(port, "/api/spoken", {"book_id": ids[-1]})[0] == 200


def test_unknown_get_is_404(port):
    assert request(port, "GET", "/../pyproject.toml")[0] == 404
    assert request(port, "GET", "/web/index.html")[0] == 404


def test_safe_stem_strips_paths_and_dots():
    assert server._safe_stem("../../etc/passwd.epub") == "passwd"
    assert server._safe_stem("...epub") == "book"
    assert server._safe_stem("a/b\\c:d.epub") == "bcd"
    assert re.fullmatch(r"[\w.\- ]+", server._safe_stem("x" * 500 + ".epub"))
    assert len(server._safe_stem("x" * 500 + ".epub")) == 120
