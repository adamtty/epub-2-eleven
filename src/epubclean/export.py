"""Write an EPUB 3 whose spine is the text the voice will read.

The cover image and the original title metadata stay in the package. ElevenLabs
uses those for the book itself, and they are not part of the spoken text.
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from lxml import etree

from epubclean.model import Book, EpubError
from epubclean.parse import parse_xml
from epubclean.render import check_preset, cleaned_body, iter_blocks, parse_overrides
from epubclean.xmlutil import tag_name

XHTML = "http://www.w3.org/1999/xhtml"
OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
OPS = "http://www.idpf.org/2007/ops"
CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
XML = "http://www.w3.org/XML/1998/namespace"
BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote", "pre"})


def destination_for(book: Book) -> Path:
    filename = f"{book.export_stem or book.source_path.stem}-elevenlabs.epub"
    if book.from_upload:
        folder = Path.home() / "Downloads"
        folder.mkdir(exist_ok=True)
        return folder / filename
    return book.source_path.with_name(filename)


def export_epub(
    book: Book, dest: Path, preset: str, overrides: dict[str, bool] | None = None
) -> Path:
    preset = check_preset(preset)
    overrides = parse_overrides(overrides)
    written: list[tuple[str, str, bytes]] = []
    for index, chapter in enumerate(book.chapters, start=1):
        body = cleaned_body(chapter, preset, overrides)
        if body is None:
            continue
        blocks = [(name if name in BLOCK_TAGS else "p", text) for name, text in iter_blocks(body)]
        if not blocks:
            continue
        filename = f"text/chapter-{index:03d}.xhtml"
        heading = chapter.heading or f"Chapter {len(written) + 1}"
        written.append((filename, heading, _xhtml(heading, blocks, book.language)))
    if not written:
        raise EpubError("Nothing left to read. Turn a chapter back on, then export again.")
    cover_name = _cover_filename(book)
    if cover_name:
        written.insert(0, ("cover.xhtml", "", _cover_xhtml(book, cover_name)))
    files = {
        "META-INF/container.xml": _container(),
        "EPUB/content.opf": _opf(book, written, cover_name),
        "EPUB/nav.xhtml": _nav(written, book.language),
    }
    if cover_name:
        files[f"EPUB/{cover_name}"] = book.cover_bytes
    for filename, _heading, payload in written:
        files[f"EPUB/{filename}"] = payload
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _write_zip(dest, files)
    return dest


def _xhtml(title: str, blocks: list[tuple[str, str]], language: str) -> bytes:
    html = etree.Element(f"{{{XHTML}}}html", nsmap={None: XHTML})
    html.set(f"{{{XML}}}lang", language or "en")
    head = etree.SubElement(html, f"{{{XHTML}}}head")
    etree.SubElement(head, f"{{{XHTML}}}title").text = title
    body = etree.SubElement(html, f"{{{XHTML}}}body")
    for name, text in blocks:
        node = etree.SubElement(body, f"{{{XHTML}}}{name}")
        node.text = text
    return etree.tostring(html, xml_declaration=True, encoding="utf-8")


def _nav(chapters: list[tuple[str, str, bytes]], language: str) -> bytes:
    html = etree.Element(f"{{{XHTML}}}html", nsmap={None: XHTML, "epub": OPS})
    html.set(f"{{{XML}}}lang", language or "en")
    head = etree.SubElement(html, f"{{{XHTML}}}head")
    etree.SubElement(head, f"{{{XHTML}}}title").text = "Contents"
    body = etree.SubElement(html, f"{{{XHTML}}}body")
    nav = etree.SubElement(body, f"{{{XHTML}}}nav")
    nav.set(f"{{{OPS}}}type", "toc")
    listing = etree.SubElement(nav, f"{{{XHTML}}}ol")
    for filename, heading, _payload in chapters:
        if not heading:
            continue
        item = etree.SubElement(listing, f"{{{XHTML}}}li")
        link = etree.SubElement(item, f"{{{XHTML}}}a")
        link.set("href", filename)
        link.text = heading
    return etree.tostring(html, xml_declaration=True, encoding="utf-8")


def _cover_filename(book: Book) -> str:
    if not book.cover_bytes:
        return ""
    media = book.cover_media
    data = book.cover_bytes
    if "png" in media or data.startswith(b"\x89PNG"):
        suffix = ".png"
    elif "jpeg" in media or data.startswith(b"\xff\xd8"):
        suffix = ".jpg"
    elif "gif" in media or data.startswith(b"GIF"):
        suffix = ".gif"
    elif "svg" in media or b"<svg" in data[:300].lower():
        suffix = ".svg"
    elif "webp" in media:
        suffix = ".webp"
    else:
        suffix = ".img"
    return f"cover{suffix}"


def _cover_xhtml(book: Book, image_name: str) -> bytes:
    html = etree.Element(f"{{{XHTML}}}html", nsmap={None: XHTML, "epub": OPS})
    html.set(f"{{{XML}}}lang", book.language or "en")
    head = etree.SubElement(html, f"{{{XHTML}}}head")
    etree.SubElement(head, f"{{{XHTML}}}title").text = book.title or "Cover"
    body = etree.SubElement(html, f"{{{XHTML}}}body")
    body.set(f"{{{OPS}}}type", "cover")
    image = etree.SubElement(body, f"{{{XHTML}}}img")
    image.set("src", image_name)
    image.set("alt", "")
    return etree.tostring(html, xml_declaration=True, encoding="utf-8")


def _opf(book: Book, chapters: list[tuple[str, str, bytes]], cover_name: str) -> bytes:
    package = etree.Element(f"{{{OPF}}}package", nsmap={None: OPF, "dc": DC})
    package.set("version", "3.0")
    package.set("unique-identifier", book.unique_identifier or "uid")
    package.set(f"{{{XML}}}lang", book.language or "en")
    package.append(_metadata_element(book, bool(cover_name)))
    manifest = etree.SubElement(package, f"{{{OPF}}}manifest")
    nav_item = etree.SubElement(manifest, f"{{{OPF}}}item")
    nav_item.set("id", "nav")
    nav_item.set("href", "nav.xhtml")
    nav_item.set("media-type", "application/xhtml+xml")
    nav_item.set("properties", "nav")
    if cover_name:
        cover_item = etree.SubElement(manifest, f"{{{OPF}}}item")
        cover_item.set("id", "cover-image")
        cover_item.set("href", cover_name)
        cover_item.set("media-type", book.cover_media or "image/jpeg")
        cover_item.set("properties", "cover-image")
    spine = etree.SubElement(package, f"{{{OPF}}}spine")
    for index, (filename, _heading, _payload) in enumerate(chapters, start=1):
        item_id = "cover" if filename == "cover.xhtml" else f"c{index}"
        item = etree.SubElement(manifest, f"{{{OPF}}}item")
        item.set("id", item_id)
        item.set("href", filename)
        item.set("media-type", "application/xhtml+xml")
        ref = etree.SubElement(spine, f"{{{OPF}}}itemref")
        ref.set("idref", item_id)
    return etree.tostring(package, xml_declaration=True, encoding="utf-8")


def _metadata_element(book: Book, has_cover: bool):
    if book.metadata_xml:
        metadata = parse_xml(book.metadata_xml)
    else:
        metadata = etree.Element(f"{{{OPF}}}metadata", nsmap={None: OPF, "dc": DC})
        identifier = etree.SubElement(metadata, f"{{{DC}}}identifier")
        identifier.set("id", book.unique_identifier or "uid")
        identifier.text = f"urn:uuid:{uuid4()}"
        etree.SubElement(metadata, f"{{{DC}}}title").text = book.title or "Untitled"
        if book.author:
            etree.SubElement(metadata, f"{{{DC}}}creator").text = book.author
        etree.SubElement(metadata, f"{{{DC}}}language").text = book.language or "en"
    _ensure_text(metadata, "title", book.title or "Untitled")
    _ensure_text(metadata, "language", book.language or "en")
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp = next(
        (
            el
            for el in metadata.iter()
            if tag_name(el) == "meta" and (el.get("property") or "") == "dcterms:modified"
        ),
        None,
    )
    if stamp is None:
        stamp = etree.SubElement(metadata, f"{{{OPF}}}meta")
        stamp.set("property", "dcterms:modified")
    stamp.text = modified
    if has_cover:
        cover_meta = next(
            (
                el
                for el in metadata.iter()
                if tag_name(el) == "meta" and (el.get("name") or "").lower() == "cover"
            ),
            None,
        )
        if cover_meta is None:
            cover_meta = etree.SubElement(metadata, f"{{{OPF}}}meta")
            cover_meta.set("name", "cover")
        cover_meta.set("content", "cover-image")
    return metadata


def _ensure_text(metadata, name: str, value: str) -> None:
    for el in metadata.iter():
        if tag_name(el) == name and "".join(el.itertext()).strip():
            return
    etree.SubElement(metadata, f"{{{DC}}}{name}").text = value


def _container() -> bytes:
    container = etree.Element(f"{{{CONTAINER}}}container", nsmap={None: CONTAINER})
    container.set("version", "1.0")
    rootfiles = etree.SubElement(container, f"{{{CONTAINER}}}rootfiles")
    rootfile = etree.SubElement(rootfiles, f"{{{CONTAINER}}}rootfile")
    rootfile.set("full-path", "EPUB/content.opf")
    rootfile.set("media-type", "application/oebps-package+xml")
    return etree.tostring(container, xml_declaration=True, encoding="utf-8")


def _write_zip(dest: Path, files: dict[str, bytes]) -> None:
    """Write beside the destination, then rename over it.

    The rename replaces whatever is at dest, a symlink included, rather than writing
    through it, and a half-written book is never left behind.
    """
    fd, temp_name = tempfile.mkstemp(prefix=".epub-2-eleven-", suffix=".tmp", dir=dest.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            _zip_into(handle, files)
        temp.chmod(0o644)
        os.replace(temp, dest)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _zip_into(handle, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(handle, "w") as archive:
        info = zipfile.ZipInfo("mimetype", date_time=(2020, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_STORED
        info.extra = b""
        archive.writestr(info, b"application/epub+zip")
        for name in sorted(files):
            archive.writestr(name, files[name], compress_type=zipfile.ZIP_DEFLATED)
