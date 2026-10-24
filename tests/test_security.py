"""Hostile EPUBs: what a crafted file must not be able to do."""

import os
import re
import zipfile
from pathlib import Path

import pytest
from lxml import html as lxml_html

from epubclean import parse
from epubclean.classify import classify_book
from epubclean.export import export_epub
from epubclean.model import EpubError
from epubclean.parse import parse_epub
from epubclean.render import chapter_preview_html, spoken_text
from tests.epubs import make_epub, semantic_book, xhtml


def load(path: Path):
    book = parse_epub(path)
    classify_book(book)
    return book


def preview(tmp_path, body: str) -> str:
    book = load(make_epub(tmp_path / "x.epub", [("text/c.xhtml", xhtml(f"<h1>One</h1>{body}"))]))
    return chapter_preview_html(book.chapters[0])


HOSTILE = [
    '<base href="https://evil.example/"/>',
    '<p><a href="javascript:alert(1)">x</a></p>',
    '<p><a href=" JaVaScRiPt:alert(1)">x</a></p>',
    '<p><a href="https://evil.example/">x</a></p>',
    '<p><img src="x" onerror="alert(1)" alt="pic"/></p>',
    '<p onclick="alert(1)" onmouseover="alert(1)">hi</p>',
    '<iframe src="https://evil.example/"></iframe>',
    '<object data="x.swf"></object>',
    '<form action="https://evil.example/"><button formaction="x">go</button></form>',
    '<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)">'
    "<text>t</text></a></svg>",
    '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi xlink:href="javascript:1"'
    ' xmlns:xlink="http://www.w3.org/1999/xlink">x</mi></math>',
    '<meta http-equiv="refresh" content="0;url=https://evil.example/"/>',
    '<link rel="stylesheet" href="https://evil.example/x.css"/>',
    '<p style="background:url(https://evil.example/)">styled</p>',
    '<p id="error">clobber</p>',
    "<script>alert(1)</script><style>*{}</style>",
    "<noscript><p>&lt;img src=x onerror=alert(1)&gt;</p></noscript>",
    '<p xmlns:x="urn:x" x:onclick="alert(1)">ns</p>',
]


@pytest.mark.parametrize("body", HOSTILE)
def test_preview_html_cannot_run_or_load_anything(tmp_path, body):
    out = preview(tmp_path, body)
    tree = lxml_html.fragment_fromstring(out, create_parent="div")
    allowed = {"class", "title", "data-kind", "data-epubclean-id", "data-epubclean-kind"}
    for el in tree.iter():
        assert el.tag not in {"base", "iframe", "object", "form", "button", "svg", "math", "a",
                              "img", "meta", "link", "script", "style", "noscript"}  # fmt: skip
        assert set(el.attrib) <= allowed, (el.tag, dict(el.attrib))


def test_preview_keeps_text_of_unwrapped_elements(tmp_path):
    out = preview(tmp_path, '<p>See <a href="https://example.com/">the map</a>.</p>')
    assert "the map" in out
    assert "href" not in out


def test_marked_noteref_stays_toggleable(tmp_path):
    book = load(semantic_book(tmp_path / "Sample.epub"))
    out = chapter_preview_html(next(c for c in book.chapters if c.role == "body"))
    assert 'data-kind="noteref"' in out
    assert not re.search(r"<a[\s>]", out)


def test_entities_are_not_expanded(tmp_path):
    # billion laughs: if the parser expanded these the chapter would be gigabytes
    lol = '<!ENTITY l0 "lol">' + "".join(
        f'<!ENTITY l{i} "{f"&l{i - 1};" * 10}">' for i in range(1, 10)
    )
    doc = (
        '<?xml version="1.0"?>\n'
        f"<!DOCTYPE html [{lol}]>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Laughs</h1><p>&l9;</p>'
        "<p>Plain words.</p></body></html>"
    )
    book = load(make_epub(tmp_path / "x.epub", [("text/c.xhtml", doc)]))
    text = spoken_text(book, "conservative")
    assert "Plain words." in text
    assert len(text) < 1000


def test_external_entities_are_not_loaded(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-VALUE")
    doc = (
        '<?xml version="1.0"?>\n'
        f'<!DOCTYPE html [<!ENTITY xxe SYSTEM "file://{secret}">]>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>X</h1><p>&xxe;</p>'
        "<p>Words.</p></body></html>"
    )
    book = load(make_epub(tmp_path / "x.epub", [("text/c.xhtml", doc)]))
    assert "TOP-SECRET-VALUE" not in spoken_text(book, "conservative")


def test_external_entity_in_package_document_is_not_loaded(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-VALUE")
    path = make_epub(
        tmp_path / "x.epub",
        [("text/c.xhtml", xhtml("<h1>X</h1><p>Words.</p>"))],
        title="&xxe;",
    )
    _rewrite_member(
        path,
        "OEBPS/content.opf",
        lambda data: data.replace(
            b"?>",
            f'?>\n<!DOCTYPE package [<!ENTITY xxe SYSTEM "file://{secret}">]>'.encode(),
            1,
        ),
    )
    try:
        book = load(path)
    except EpubError:
        return
    assert "TOP-SECRET-VALUE" not in book.title


def test_zip_bomb_member_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(parse, "MAX_MEMBER", 1024)
    big = "<p>" + "a" * 5000 + "</p>"
    path = make_epub(tmp_path / "x.epub", [("text/c.xhtml", xhtml(f"<h1>Big</h1>{big}"))])
    with pytest.raises(EpubError, match="too large"):
        parse_epub(path)


def test_lying_zip_size_header_is_still_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(parse, "MAX_MEMBER", 1024)
    big = "<p>" + "a" * 5000 + "</p>"
    path = make_epub(tmp_path / "x.epub", [("text/c.xhtml", xhtml(f"<h1>Big</h1>{big}"))])
    _shrink_declared_size(path, "OEBPS/text/c.xhtml", 10)
    with pytest.raises(EpubError):
        parse_epub(path)


def test_total_unpacked_size_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(parse, "MAX_TOTAL", 4096)
    docs = [(f"text/c{i}.xhtml", xhtml(f"<h1>C{i}</h1><p>{'b' * 1500}</p>")) for i in range(5)]
    with pytest.raises(EpubError, match="unpacks"):
        parse_epub(make_epub(tmp_path / "x.epub", docs))


def test_deep_nesting_is_a_clean_error_or_reads(tmp_path):
    deep = "<div>" * 5000 + "<p>Deep.</p>" + "</div>" * 5000
    path = make_epub(tmp_path / "x.epub", [("text/c.xhtml", xhtml(f"<h1>Deep</h1>{deep}"))])
    try:
        book = load(path)
    except EpubError:
        return
    spoken_text(book, "narration")


@pytest.mark.parametrize("data", [b"", b"PK\x03\x04garbage", b"not a zip at all"])
def test_junk_files_are_clean_errors(tmp_path, data):
    path = tmp_path / "x.epub"
    path.write_bytes(data)
    with pytest.raises(EpubError):
        parse_epub(path)


def test_missing_package_document_is_a_clean_error(tmp_path):
    path = tmp_path / "x.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="nope.opf"/></rootfiles></container>',
        )
    with pytest.raises(EpubError):
        parse_epub(path)


def test_export_replaces_a_symlink_instead_of_writing_through_it(tmp_path):
    book = load(semantic_book(tmp_path / "Sample.epub"))
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    dest = tmp_path / "out.epub"
    dest.symlink_to(victim)
    export_epub(book, dest, "narration")
    assert victim.read_text() == "keep me"
    assert not dest.is_symlink()
    assert zipfile.is_zipfile(dest)


def test_failed_export_leaves_no_temp_file(tmp_path, monkeypatch):
    book = load(semantic_book(tmp_path / "Sample.epub"))
    out = tmp_path / "out"
    out.mkdir()

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        export_epub(book, out / "book.epub", "narration")
    assert list(out.iterdir()) == []


def _rewrite_member(path: Path, name: str, change) -> None:
    with zipfile.ZipFile(path) as source:
        members = [(info, source.read(info)) for info in source.infolist()]
    with zipfile.ZipFile(path, "w") as target:
        for info, data in members:
            target.writestr(info, change(data) if info.filename == name else data)


def _shrink_declared_size(path: Path, name: str, size: int) -> None:
    """Patch the central directory so the member claims to be tiny."""
    data = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(name)
    encoded = name.encode()
    central = data.find(b"PK\x01\x02")
    while central != -1:
        name_len = int.from_bytes(data[central + 28 : central + 30], "little")
        if data[central + 46 : central + 46 + name_len] == encoded:
            data[central + 24 : central + 28] = size.to_bytes(4, "little")
            break
        central = data.find(b"PK\x01\x02", central + 4)
    local = info.header_offset
    data[local + 22 : local + 26] = size.to_bytes(4, "little")
    path.write_bytes(bytes(data))


# Found by fuzzing (tests/fuzz_*.py); the crashing inputs are in tests/fuzz-corpus/.


def test_control_characters_in_a_chapter_are_dropped(tmp_path):
    # not well-formed, so it goes through the HTML parser, which keeps &#1; as a raw U+0001
    doc = "<html><body><h1>Ctl</h1><p>Be&#1;fore 1. After&#8;.</p><p x\x01y='a\x02b'>z</p>"
    book = load(make_epub(tmp_path / "x.epub", [("text/c.xhtml", doc)]))
    text = spoken_text(book, "conservative")
    assert "Before" in text
    assert "\x01" not in text and "\x08" not in text
    export_epub(book, tmp_path / "out.epub", "conservative")


def test_fuzz_corpus_regressions_read_cleanly(tmp_path):
    corpus = Path(__file__).parent / "fuzz-corpus"
    for seed in sorted((corpus / "chapter").glob("regression-*")):
        raw = seed.read_bytes()
        path = make_epub(tmp_path / f"{seed.name}.epub", [("text/c.xhtml", "")])
        _rewrite_member(path, "OEBPS/text/c.xhtml", lambda _data, raw=raw: raw)
        try:
            book = load(path)
        except EpubError:
            continue
        spoken_text(book, "narration")
        for item in book.chapters:
            chapter_preview_html(item)


def test_text_after_metadata_does_not_break_export(tmp_path):
    path = make_epub(tmp_path / "x.epub", [("text/c.xhtml", xhtml("<h1>One</h1><p>Words.</p>"))])
    _rewrite_member(
        path, "OEBPS/content.opf", lambda data: data.replace(b"</metadata>", b"</metadata>stray", 1)
    )
    book = load(path)
    export_epub(book, tmp_path / "out.epub", "narration")
    assert zipfile.is_zipfile(tmp_path / "out.epub")
