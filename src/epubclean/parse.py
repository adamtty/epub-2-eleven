"""Read a DRM-free EPUB into spine order without rewriting its prose."""

from __future__ import annotations

import html as html_stdlib
import posixpath
import re
import zipfile
import zlib
from pathlib import Path
from urllib.parse import unquote

from lxml import etree
from lxml import html as lxml_html

from epubclean.model import Book, Chapter, DRMError, EpubError, FixedLayoutError
from epubclean.xmlutil import collapse, find_body, tag_name, visible_text

_NAMED_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]+);")
_KEEP_ENTITIES = frozenset({"lt", "gt", "amp", "quot", "apos"})
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".bmp")
_DOC_SUFFIXES = (".xhtml", ".html", ".htm", ".xml")
_DOC_TYPES = frozenset({"application/xhtml+xml", "text/html", "application/xml", "text/xml"})
# A small EPUB can claim to unpack into gigabytes. These caps stop a zip bomb.
MAX_MEMBER = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
_ZIP_ERRORS = (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError, OSError)


class _Archive:
    """The book's zip, with every read capped in size."""

    def __init__(self, zf: zipfile.ZipFile) -> None:
        self.zf = zf
        self.budget = MAX_TOTAL

    def read(self, name: str) -> bytes:
        info = self.zf.getinfo(name)
        if info.file_size > MAX_MEMBER:
            raise EpubError(f"{name} is too large to read.")
        try:
            with self.zf.open(info) as handle:
                data = handle.read(MAX_MEMBER + 1)
        except _ZIP_ERRORS as exc:
            raise EpubError(f"{name} in this EPUB is damaged.") from exc
        if len(data) > MAX_MEMBER:
            raise EpubError(f"{name} is too large to read.")
        self.budget -= len(data)
        if self.budget < 0:
            raise EpubError("This EPUB unpacks to more than it should.")
        return data


def _xml_parser(*, recover: bool = False) -> etree.XMLParser:
    """No DTDs, no entity expansion, no network, and libxml2's size limits left on."""
    return etree.XMLParser(recover=recover, resolve_entities=False, no_network=True, load_dtd=False)


def parse_xml(data: bytes):
    return etree.fromstring(data, _xml_parser())


def parse_epub(path: Path) -> Book:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise EpubError(f"No file at {path}.")
    if path.suffix.lower() != ".epub":
        raise EpubError("Choose an .epub file.")
    try:
        zf = zipfile.ZipFile(path)
    except (*_ZIP_ERRORS, ValueError) as exc:
        raise EpubError("This file is not a valid EPUB.") from exc
    with zf:
        names = set(zf.namelist())
        archive = _Archive(zf)
        if any(name.lower() == "meta-inf/encryption.xml" for name in names):
            raise DRMError(
                "This EPUB is locked with DRM. epub-2-eleven only works on unlocked files."
            )
        rootfile = _rootfile(archive)
        opf_dir = posixpath.dirname(rootfile)
        try:
            opf = parse_xml(archive.read(rootfile))
        except KeyError as exc:
            raise EpubError("The book's package document is missing.") from exc
        except etree.XMLSyntaxError as exc:
            raise EpubError("The book's package document could not be read.") from exc
        if _is_fixed_layout(opf):
            raise FixedLayoutError(
                "This EPUB is fixed-layout (pre-paginated). "
                "The page furniture is the page itself, so there is nothing safe to strip."
            )
        title, author, language = _metadata(opf, path)
        manifest = _manifest(opf, opf_dir)
        spine = _spine(opf)
        guide = _guide(opf, opf_dir)
        cover_media, cover_bytes = _cover_image(archive, opf, manifest, guide, spine)
        nav_labels, landmarks = _navigation(archive, manifest, opf_dir)
        chapters: list[Chapter] = []
        for index, idref in enumerate(spine):
            item = manifest.get(idref)
            if item is None:
                raise EpubError(f"Spine item {idref} is missing from the manifest.")
            chapter = _load_chapter(archive, index, item, nav_labels)
            if chapter is not None:
                chapters.append(chapter)
    if not chapters:
        raise EpubError("This EPUB has no chapter documents to read.")
    return Book(
        source_path=path,
        title=title,
        author=author,
        language=language,
        chapters=chapters,
        guide=guide,
        landmarks=landmarks,
        nav_labels=nav_labels,
        export_stem=path.stem,
        unique_identifier=opf.get("unique-identifier") or "uid",
        metadata_xml=_metadata_xml(opf),
        cover_media=cover_media,
        cover_bytes=cover_bytes,
    )


def parse_markup(data: bytes):
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    parser = _xml_parser(recover=True)
    root = None
    try:
        root = etree.fromstring(_expand_named_entities(data), parser)
    except etree.XMLSyntaxError:
        root = None
    fatal = any(error.level_name == "FATAL" for error in parser.error_log)
    if root is None or not isinstance(root.tag, str) or fatal:
        try:
            root = lxml_html.fromstring(data)
        except (etree.ParserError, ValueError) as exc:
            raise EpubError("A document in this EPUB could not be read.") from exc
    _drop_control_chars(root)
    return root


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _drop_control_chars(root) -> None:
    """The HTML parser keeps characters XML forbids, and lxml refuses to write them back."""
    for el in root.iter():
        if el.text and _CONTROL.search(el.text):
            el.text = _CONTROL.sub("", el.text)
        if el.tail and _CONTROL.search(el.tail):
            el.tail = _CONTROL.sub("", el.tail)
        if not isinstance(el.tag, str):
            continue
        attrs = list(el.attrib.items())
        if not any(_CONTROL.search(key) or _CONTROL.search(value) for key, value in attrs):
            continue
        el.attrib.clear()
        for key, value in attrs:
            try:
                el.set(_CONTROL.sub("", key), _CONTROL.sub("", value))
            except ValueError:
                continue  # a name no XML document could have


def _expand_named_entities(data: bytes) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in _KEEP_ENTITIES:
            return match.group(0)
        unescaped = html_stdlib.unescape(match.group(0))
        return match.group(0) if unescaped == match.group(0) else unescaped

    return _NAMED_ENTITY.sub(replace, text).encode("utf-8")


def _rootfile(zf: _Archive) -> str:
    try:
        data = zf.read("META-INF/container.xml")
    except KeyError as exc:
        raise EpubError("This file is not an EPUB (missing META-INF/container.xml).") from exc
    try:
        root = parse_xml(data)
    except etree.XMLSyntaxError as exc:
        raise EpubError("This EPUB's container.xml could not be read.") from exc
    for el in root.iter():
        if tag_name(el) == "rootfile" and el.get("full-path"):
            return el.get("full-path")
    raise EpubError("This EPUB has no package document.")


def _is_fixed_layout(opf) -> bool:
    for el in opf.iter():
        if tag_name(el) != "meta":
            continue
        prop = (el.get("property") or el.get("name") or "").strip().lower()
        content = (el.text or el.get("content") or "").strip().lower()
        if prop == "rendition:layout" and content == "pre-paginated":
            return True
    return False


def _metadata(opf, path: Path) -> tuple[str, str, str]:
    titles = [_element_text(el) for el in opf.iter() if tag_name(el) == "title"]
    creators = [_element_text(el) for el in opf.iter() if tag_name(el) == "creator"]
    languages = [_element_text(el) for el in opf.iter() if tag_name(el) == "language"]
    title = next((item for item in titles if item), "") or path.stem
    author = ", ".join(item for item in creators if item)
    language = next((item for item in languages if item), "") or "en"
    return title, author, language


def _element_text(el) -> str:
    return collapse("".join(el.itertext()))


def _metadata_xml(opf) -> bytes:
    for el in opf:
        if tag_name(el) == "metadata":
            return etree.tostring(el, encoding="utf-8", with_tail=False)
    return b""


def _cover_image(zf, opf, manifest, guide, spine) -> tuple[str, bytes]:
    """The jacket image ElevenLabs shows, not the words on the title page."""
    for item in manifest.values():
        if "cover-image" in item["properties"].split():
            found = _image_bytes(zf, item["href"], item["media"])
            if found:
                return found
    for el in opf.iter():
        if tag_name(el) == "meta" and (el.get("name") or "").lower() == "cover":
            item = manifest.get(el.get("content") or "")
            if item:
                found = _image_bytes(zf, item["href"], item["media"])
                if found:
                    return found
    for idref in spine:
        item = manifest.get(idref)
        if item is None:
            continue
        href = item["href"]
        if guide.get(href) == "cover" or "cover" in Path(href).stem.casefold():
            if _is_image(item["media"], href):
                found = _image_bytes(zf, href, item["media"])
                if found:
                    return found
            found = _image_inside_document(zf, href, manifest)
            if found:
                return found
    return "", b""


def _image_inside_document(zf, href: str, manifest) -> tuple[str, bytes] | None:
    try:
        tree = parse_markup(zf.read(href))
    except KeyError:
        return None
    base = posixpath.dirname(href)
    for el in tree.iter():
        if tag_name(el) != "img" or not el.get("src"):
            continue
        path = _zip_href(base, el.get("src"))
        media = next((item["media"] for item in manifest.values() if item["href"] == path), "")
        found = _image_bytes(zf, path, media)
        if found:
            return found
    return None


def _image_bytes(zf, href: str, media: str) -> tuple[str, bytes] | None:
    try:
        data = zf.read(href)
    except KeyError:
        return None
    if not data:
        return None
    return _image_type(media, href), data


def _image_type(media: str, href: str) -> str:
    if media.startswith("image/"):
        return media
    suffix = href.rsplit(".", 1)[-1].lower()
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "svg": "image/svg+xml",
        "webp": "image/webp",
    }.get(suffix, "image/jpeg")


def _manifest(opf, opf_dir: str) -> dict[str, dict[str, str]]:
    items: dict[str, dict[str, str]] = {}
    for el in opf.iter():
        if tag_name(el) != "item" or tag_name(el.getparent()) != "manifest":
            continue
        item_id = el.get("id")
        href = el.get("href")
        if not item_id or not href:
            continue
        items[item_id] = {
            "href": _zip_href(opf_dir, href),
            "media": (el.get("media-type") or "").lower(),
            "properties": el.get("properties") or "",
        }
    return items


def _spine(opf) -> list[str]:
    refs: list[str] = []
    for el in opf.iter():
        if tag_name(el) != "itemref" or tag_name(el.getparent()) != "spine":
            continue
        idref = el.get("idref")
        if idref:
            refs.append(idref)
    return refs


def _guide(opf, opf_dir: str) -> dict[str, str]:
    guide: dict[str, str] = {}
    for el in opf.iter():
        if tag_name(el) != "reference" or tag_name(el.getparent()) != "guide":
            continue
        href = el.get("href")
        kind = (el.get("type") or "").strip().lower()
        if href and kind:
            guide[_zip_href(opf_dir, href)] = kind
    return guide


def _navigation(zf: _Archive, manifest: dict[str, dict[str, str]], opf_dir: str):
    labels: dict[str, str] = {}
    landmarks: dict[str, str] = {}
    for item in manifest.values():
        props = set(item["properties"].split())
        href = item["href"]
        if (
            "nav" in props
            or href.lower().endswith(".ncx")
            or item["media"] == "application/x-dtbncx+xml"
        ):
            try:
                data = zf.read(href)
            except KeyError:
                continue
            tree = parse_markup(data)
            if href.lower().endswith(".ncx") or item["media"] == "application/x-dtbncx+xml":
                _read_ncx(tree, opf_dir, href, labels)
            else:
                _read_nav(tree, opf_dir, href, labels, landmarks)
    return labels, landmarks


def _read_nav(
    tree, opf_dir: str, nav_href: str, labels: dict[str, str], landmarks: dict[str, str]
) -> None:
    from epubclean.xmlutil import epub_types

    navs = [el for el in tree.iter() if tag_name(el) == "nav"]
    if not navs:
        navs = [tree]
    for nav in navs:
        types = set(epub_types(nav))
        is_landmarks = "landmarks" in types
        is_toc = "toc" in types or not types
        for anchor in nav.iter():
            if tag_name(anchor) != "a" or not anchor.get("href"):
                continue
            target = _zip_href(posixpath.dirname(nav_href) or opf_dir, anchor.get("href"))
            text = collapse(visible_text(anchor))
            if is_toc and text and target not in labels:
                labels[target] = text
            if is_landmarks:
                role_tokens = epub_types(anchor)
                if role_tokens:
                    landmarks[target] = role_tokens[0]


def _read_ncx(tree, opf_dir: str, ncx_href: str, labels: dict[str, str]) -> None:
    base = posixpath.dirname(ncx_href) or opf_dir
    for point in tree.iter():
        if tag_name(point) != "navpoint":
            continue
        label = ""
        src = ""
        for child in point.iter():
            if tag_name(child) == "text" and not label:
                label = collapse(visible_text(child))
            if tag_name(child) == "content" and child.get("src"):
                src = child.get("src")
        if label and src:
            labels.setdefault(_zip_href(base, src), label)


def _load_chapter(zf: _Archive, index: int, item: dict[str, str], nav_labels: dict[str, str]):
    href = item["href"]
    media = item["media"]
    if _is_image(media, href):
        tree = parse_markup(
            (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                '<img alt="Cover"/></body></html>'
            ).encode()
        )
        return Chapter(
            id=f"c{index}",
            href=href,
            heading=nav_labels.get(href) or "Cover",
            role="cover",
            reason="The spine item is an image.",
            tree=tree,
            spine_kind="image",
        )
    if not _is_document(media, href):
        return None
    try:
        data = zf.read(href)
    except KeyError as exc:
        raise EpubError(f"The book is missing {href}.") from exc
    tree = parse_markup(data)
    heading = _first_heading(tree) or nav_labels.get(href) or Path(href).stem or "Untitled"
    return Chapter(
        id=f"c{index}",
        href=href,
        heading=heading,
        role="body",
        reason="",
        tree=tree,
    )


def _first_heading(tree) -> str:
    for el in find_body(tree).iter():
        if tag_name(el) in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            text = collapse(visible_text(el))
            if text:
                return text
    return ""


def _is_image(media: str, href: str) -> bool:
    return media.startswith("image/") or href.lower().endswith(_IMAGE_SUFFIXES)


def _is_document(media: str, href: str) -> bool:
    return media in _DOC_TYPES or href.lower().endswith(_DOC_SUFFIXES)


def _zip_href(base: str, href: str) -> str:
    path = unquote(href.split("#", 1)[0]).strip()
    if not path:
        return ""
    joined = path if path.startswith("/") else posixpath.join(base, path)
    normalized = posixpath.normpath(joined)
    return normalized.lstrip("/")
