"""Coverage-guided fuzzing of the package document and navigation with Atheris (Linux x86-64).

The input is the OPF and the nav document, split at the first NUL byte. Manifest paths, spine
references, guide entries, cover lookups and the table of contents all come from it, so a hostile
package must either read or raise EpubError, never crash or reach outside the zip.

    uv pip install --require-hashes --only-binary :all: -r tests/requirements-fuzz.txt
    python tests/fuzz_package.py tests/fuzz-corpus/package -max_total_time=60
"""

import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import atheris  # noqa: E402

with atheris.instrument_imports():
    from epubclean.classify import classify_book
    from epubclean.export import export_epub
    from epubclean.model import EpubError
    from epubclean.parse import parse_epub
    from epubclean.render import book_view

WORK = Path(tempfile.mkdtemp(prefix="epub-2-eleven-fuzz-"))
SOURCE = WORK / "fuzz.epub"
OUT = WORK / "out.epub"

CONTAINER = b"""<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
</rootfiles></container>"""

CHAPTERS = {
    "OEBPS/text/cover.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
    b'<img src="../images/cover.png" alt="Cover"/></body></html>',
    "OEBPS/text/c1.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
    b"<h1>One</h1><p>First words.</p><p>12</p></body></html>",
    "OEBPS/text/c2.xhtml": b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
    b"<h1>Contents</h1><p>One</p></body></html>",
    "OEBPS/images/cover.png": b"\x89PNG\r\n\x1a\nnot-really",
}


def build(opf: bytes, nav: bytes) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/nav.xhtml", nav)
        archive.writestr("OEBPS/toc.ncx", nav)
        for name, payload in CHAPTERS.items():
            archive.writestr(name, payload)
    SOURCE.write_bytes(buffer.getvalue())


def TestOneInput(data: bytes) -> None:
    opf, _sep, nav = data.partition(b"\0")
    build(opf, nav)
    try:
        book = parse_epub(SOURCE)
        classify_book(book)
    except EpubError:
        return
    book_view(book)
    try:
        export_epub(book, OUT, "narration")
    except EpubError:
        return


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
