from pathlib import Path
import zipfile

from epubclean.classify import classify_book
from epubclean.export import export_epub
from epubclean.model import DRMError, FixedLayoutError
from epubclean.parse import parse_epub
from epubclean.render import chapter_preview_html, spoken_text
from tests.epubs import hyphen_book, make_epub, pdf_book, semantic_book, xhtml


def load(path: Path):
    book = parse_epub(path)
    classify_book(book)
    return book


def chapter(book, role: str):
    return next(item for item in book.chapters if item.role == role)


def test_narration_keeps_the_story_and_drops_furniture(tmp_path):
    book = load(semantic_book(tmp_path / "Sample.epub"))
    text = spoken_text(book, "narration")

    assert "Before the road." in text
    assert "She walked in." in text
    assert "The Road" in text
    for leftover in (
        "All rights reserved.",
        "For you.",
        "Read this first.",
        "Context matters.",
        "Later on.",
        "A note.",
        "The valley.",
        "A map of the road",
        "RUNNING",
        "47",
        "Cover",
    ):
        assert leftover not in text
    assert "1" not in text
    assert chapter(book, "prologue").role == "prologue"
    assert chapter(book, "preface").reason.startswith("Heading is")


def test_conservative_keeps_notes_and_front_matter(tmp_path):
    book = load(semantic_book(tmp_path / "Sample.epub"))
    text = spoken_text(book, "conservative")

    for kept in (
        "For you.",
        "Read this first.",
        "Context matters.",
        "Before the road.",
        "She walked in.",
        "A note.",
        "The valley.",
        "A map of the road",
        "Later on.",
    ):
        assert kept in text
    for dropped in ("All rights reserved.", "RUNNING", "47", "Cover"):
        assert dropped not in text
    assert "1" not in text


def test_repeated_headers_and_page_numbers(tmp_path):
    book = load(pdf_book(tmp_path / "Gazette.epub"))
    text = spoken_text(book, "narration")

    assert "BOOK TITLE" not in text
    assert "14" not in text and "15" not in text and "16" not in text
    assert "Alpha text here." in text
    assert "Beta text here." in text
    assert "Gamma text here." in text
    assert text.index("Chapter One") < text.index("Chapter Two") < text.index("Chapter Three")
    header = next(mark for mark in book.chapters[0].marks if mark.text == "BOOK TITLE")
    assert header.kind == "header"


def test_drop_caps_and_hyphens(tmp_path):
    book = load(hyphen_book(tmp_path / "Words.epub"))
    text = spoken_text(book, "narration")

    assert "The night." in text
    assert "Say hello now." in text
    assert "A hello there." in text
    assert "Meet Jean-Luc today." in text
    assert "hel-" not in text
    assert "\u00ad" not in text


def test_override_puts_a_cut_back(tmp_path):
    book = load(semantic_book(tmp_path / "Sample.epub"))
    copyright_page = chapter(book, "copyright")
    page = next(mark for mark in chapter(book, "body").marks if mark.text == "47")

    assert "All rights reserved." in spoken_text(book, "narration", {copyright_page.id: True})
    assert "47" in spoken_text(book, "narration", {page.id: True})
    intro = chapter(book, "introduction")
    assert "Context matters." not in spoken_text(book, "conservative", {intro.id: False})


def test_superscript_references_are_removed(tmp_path):
    body = (
        "<h1>The Quote</h1>"
        "<p>He called it a weapon.”<sup>11</sup> I agreed.</p>"
        "<p>Plain unicode.”¹¹ Next.</p>"
        "<p>Area is 4 m<sup>2</sup> today.</p>"
        "<p>The 1<sup>st</sup> day.</p>"
        "<p>demanding documentary proof for popularly-held beliefs.”13</p>"
        "<p>Her book had taken on a broader significance.2</p>"
        "<p>Pi is about 3.14 and the year was 2013.</p>"
    )
    book = load(make_epub(tmp_path / "refs.epub", [("text/c.xhtml", xhtml(body))]))
    for preset in ("narration", "conservative"):
        text = spoken_text(book, preset)
        assert "He called it a weapon.” I agreed." in text
        assert "Plain unicode.” Next." in text
        assert "11" not in text
        assert "¹¹" not in text
        assert "Area is 4 m2 today." in text
        assert "The 1st day." in text
        assert "popularly-held beliefs.”" in text
        assert "beliefs.”13" not in text
        assert "broader significance." in text
        assert "significance.2" not in text
        assert "3.14" in text
        assert "2013" in text


def test_preview_marks_the_page_number(tmp_path):
    book = load(semantic_book(tmp_path / "Sample.epub"))
    html = chapter_preview_html(chapter(book, "body"))
    assert "data-epubclean-id" in html
    assert "RUNNING" in html
    assert "Page number." in html


def test_export_matches_the_spoken_text(tmp_path):
    source = semantic_book(tmp_path / "Sample.epub")
    book = load(source)
    expected = spoken_text(book, "narration")
    dest = export_epub(book, tmp_path / "Sample-elevenlabs.epub", "narration")

    with zipfile.ZipFile(dest) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        assert archive.read("EPUB/cover.png") == b"png-cover-bytes"
        opf = archive.read("EPUB/content.opf").decode()
        assert "META-INF/encryption.xml" not in archive.namelist()
        assert "Test House" in opf
        assert "A road." in opf
        assert "urn:uuid:test" in opf
        assert 'properties="cover-image"' in opf

    cleaned = load(dest)
    assert cleaned.title == "Sample"
    assert cleaned.author == "Ada Writer"
    assert cleaned.language == "en"
    assert cleaned.chapters[0].role == "cover"
    assert len(cleaned.chapters) == 3
    assert spoken_text(cleaned, "conservative") == expected
    assert "Test House" not in expected


def test_drm_and_fixed_layout_are_refused(tmp_path):
    locked = make_epub(
        tmp_path / "locked.epub",
        [("text/c.xhtml", xhtml("<h1>Secret</h1><p>Hidden.</p>"))],
        encrypted=True,
    )
    picture = make_epub(
        tmp_path / "picture.epub",
        [("text/c.xhtml", xhtml("<h1>Picture</h1><p>A page.</p>"))],
        pre_paginated=True,
    )
    try:
        parse_epub(locked)
        raise AssertionError("DRM should be refused")
    except DRMError as exc:
        assert "DRM" in str(exc)
    try:
        parse_epub(picture)
        raise AssertionError("fixed layout should be refused")
    except FixedLayoutError as exc:
        assert "fixed-layout" in str(exc)
