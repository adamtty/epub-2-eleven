"""Coverage-guided fuzzing of one chapter's markup with Atheris (Linux x86-64; CI runs it).

The chapter is a stranger's XHTML, so whatever it contains, cleaning it must either work or raise
EpubError, and the preview HTML it produces must hold only the tags and attributes the preview
allows: anything else could run in the page.

    uv pip install --require-hashes --only-binary :all: -r tests/requirements-fuzz.txt
    python tests/fuzz_chapter.py tests/fuzz-corpus/chapter -max_total_time=60
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
    from lxml import html as lxml_html

    from epubclean.classify import classify_book
    from epubclean.export import export_epub
    from epubclean.model import EpubError
    from epubclean.parse import parse_epub
    from epubclean.presets import PRESETS
    from epubclean.render import SAFE_ATTRS, SAFE_TAGS, chapter_preview_html, spoken_document

WORK = Path(tempfile.mkdtemp(prefix="epub-2-eleven-fuzz-"))
SOURCE = WORK / "fuzz.epub"
OUT = WORK / "out.epub"

CONTAINER = b"""<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
</rootfiles></container>"""

OPF = b"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">x</dc:identifier>
<dc:title>Fuzz</dc:title><dc:language>en</dc:language></metadata>
<manifest><item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>
<item id="d" href="d.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="c"/><itemref idref="d"/></spine></package>"""


def build(chapter: bytes) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", OPF)
        archive.writestr("OEBPS/c.xhtml", chapter)
        # the same chapter twice, so the repeated-header detection has something to compare
        archive.writestr("OEBPS/d.xhtml", chapter)
    SOURCE.write_bytes(buffer.getvalue())


def check_preview(markup: str) -> None:
    if not markup.strip():
        return
    tree = lxml_html.fragment_fromstring(markup, create_parent="div")
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        assert el.tag in SAFE_TAGS or el.tag == "span", el.tag
        assert set(el.attrib) <= SAFE_ATTRS, (el.tag, dict(el.attrib))


def TestOneInput(data: bytes) -> None:
    build(data)
    try:
        book = parse_epub(SOURCE)
        classify_book(book)
    except EpubError:
        return
    for preset in PRESETS:
        spoken_document(book, preset)
    for chapter in book.chapters:
        check_preview(chapter_preview_html(chapter))
    try:
        export_epub(book, OUT, "conservative")
    except EpubError:
        return


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
