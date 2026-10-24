"""Turn keep/drop decisions into the HTML and the plain text the voice reads.

Export uses this same function, so the preview cannot drift from the file.
"""

from __future__ import annotations

import re
from copy import deepcopy
from html import escape

from epubclean.model import Book, Chapter, EpubError
from epubclean.presets import PRESETS, block_drop, chapter_drop, is_included
from epubclean.xmlutil import (
    collapse,
    find_body,
    replace_with_text,
    strip_invisible,
    tag_name,
    visible_text,
)

DROP_CAP_CLASS = re.compile(r"drop[-_]?cap|first[-_]?letter", re.IGNORECASE)
LEAF_BLOCKS = frozenset(
    {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "figcaption", "td", "th"}
)
NESTED_BLOCKS = LEAF_BLOCKS | {"div", "section", "ul", "ol", "aside", "figure", "table"}
# The preview shows a stranger's markup inside the page, so only plain text formatting gets
# through. Any other element (base, iframe, svg, form, a, ...) is unwrapped to its text.
SAFE_TAGS = frozenset(
    {
        "abbr", "article", "aside", "b", "blockquote", "br", "caption", "cite", "code", "dd",
        "del", "dfn", "div", "dl", "dt", "em", "figcaption", "figure", "footer", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "i", "ins", "kbd", "li", "mark", "ol", "p",
        "pre", "q", "s", "samp", "section", "small", "span", "strong", "sub", "sup", "table",
        "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul", "var",
    }
)  # fmt: skip
SAFE_ATTRS = frozenset({"class", "title", "data-kind", "data-epubclean-id", "data-epubclean-kind"})
VOID_PREVIEW = frozenset({"br", "hr"})


def spoken_document(
    book: Book, preset: str, overrides: dict[str, bool] | None = None
) -> tuple[str, dict[str, int]]:
    preset = check_preset(preset)
    overrides = overrides or {}
    parts: list[str] = []
    counts: dict[str, int] = {}
    for chapter in book.chapters:
        body = cleaned_body(chapter, preset, overrides)
        if body is None:
            counts[chapter.id] = 0
            continue
        text = blocks_to_text(body)
        counts[chapter.id] = len(text)
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip(), counts


def spoken_text(book: Book, preset: str, overrides: dict[str, bool] | None = None) -> str:
    text, _counts = spoken_document(book, preset, overrides)
    return text


def cleaned_body(chapter: Chapter, preset: str, overrides: dict[str, bool]):
    """A copy of the chapter body with dropped blocks removed and prose normalized."""
    preset = check_preset(preset)
    if not is_included(chapter.id, chapter_drop(chapter.role, preset), overrides):
        return None
    body = deepcopy(find_body(chapter.tree))
    marks = {mark.id: mark for mark in chapter.marks}
    for el in list(body.iter()):
        mark_id = el.get("data-epubclean-id")
        mark = marks.get(mark_id or "")
        if mark is None:
            continue
        if not is_included(mark.id, block_drop(mark.kind, preset), overrides):
            replace_with_text(el, "")
    for el in list(body.iter()):
        if tag_name(el) in {"script", "style"}:
            replace_with_text(el, "")
    _normalize_tree(body)
    for el in list(body.iter()):
        if tag_name(el) == "img":
            _image_to_paragraph(el)
    _drop_empty(body)
    if not collapse(visible_text(body)):
        return None
    return body


def iter_blocks(body):
    """Leaf blocks in reading order. Export and the spoken preview both use this."""
    for el in body.iter():
        name = tag_name(el)
        if name not in LEAF_BLOCKS or _has_nested_block(el):
            continue
        text = collapse(el.text or visible_text(el))
        if text:
            yield name, text


def blocks_to_text(body) -> str:
    return "\n\n".join(text for _name, text in iter_blocks(body)).strip()


def chapter_preview_html(chapter: Chapter) -> str:
    body = deepcopy(find_body(chapter.tree))
    marks = {mark.id: mark for mark in chapter.marks}
    for el in list(body.iter()):
        if not isinstance(getattr(el, "tag", None), str):
            continue
        if tag_name(el) in {"script", "style"}:
            replace_with_text(el, "")
            continue
        current = _preview_image(el) if tag_name(el) == "img" else el
        mark = marks.get(current.get("data-epubclean-id") or "")
        if mark is None:
            continue
        classes = [
            part for part in (current.get("class") or "").split() if part and part != "block"
        ]
        classes.append("block")
        current.set("class", " ".join(classes))
        current.set("data-kind", mark.kind)
        if mark.reason:
            current.set("title", mark.reason)
    return _inner_html(body)


def book_view(
    book: Book, preset: str = "narration", overrides: dict[str, bool] | None = None
) -> dict:
    preset = check_preset(preset)
    overrides = overrides or {}
    spoken, counts = spoken_document(book, preset, overrides)
    chapters = []
    for chapter in book.chapters:
        chapters.append(
            {
                "id": chapter.id,
                "heading": chapter.heading,
                "role": chapter.role,
                "reason": chapter.reason,
                "html": chapter_preview_html(chapter),
                "original_chars": chapter.original_chars,
                "drop": {name: chapter_drop(chapter.role, name) for name in PRESETS},
                "blocks": [
                    {
                        "id": mark.id,
                        "kind": mark.kind,
                        "reason": mark.reason,
                        "text": mark.text,
                        "drop": {name: block_drop(mark.kind, name) for name in PRESETS},
                    }
                    for mark in chapter.marks
                ],
            }
        )
    return {
        "title": book.title,
        "author": book.author,
        "language": book.language,
        "source_name": f"{book.export_stem or book.source_path.stem}.epub",
        "original_chars": sum(chapter.original_chars for chapter in book.chapters),
        "chapters": chapters,
        "spoken": {"text": spoken, "chars": len(spoken), "chapters": counts},
    }


def check_preset(preset: str) -> str:
    if preset not in PRESETS:
        raise EpubError("Unknown preset. Choose narration or conservative.")
    return preset


def parse_overrides(raw: object) -> dict[str, bool]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise EpubError("Overrides must be a map of id to true or false.")
    overrides: dict[str, bool] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, bool):
            raise EpubError("Overrides must be a map of id to true or false.")
        overrides[key] = value
    return overrides


def _normalize_tree(body) -> None:
    for el in list(body.iter()):
        if tag_name(el) not in LEAF_BLOCKS or _has_nested_block(el):
            continue
        _normalize_leaf(el)


def _has_nested_block(el) -> bool:
    return any(tag_name(child) in NESTED_BLOCKS for child in el)


def _normalize_leaf(el) -> None:
    _unwrap_drop_caps(el)
    for img in [node for node in el.iter() if tag_name(node) == "img"]:
        replace_with_text(img, collapse(img.get("alt") or ""))
    for br in [node for node in el.iter() if tag_name(node) == "br"]:
        replace_with_text(br, "\n")
    strip_invisible(el)
    text = visible_text(el).translate(
        dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\ufeff"), None)
    )
    text = re.sub(r"([A-Za-z])-\s+([a-z])", r"\1\2", text)
    text = re.sub(r"([A-Za-z])-\s+([A-Z])", r"\1-\2", text)
    text = _strip_superscript_refs(text)
    text = collapse(text)
    for child in list(el):
        el.remove(child)
    el.text = text


_SUPER_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"


def _strip_superscript_refs(text: str) -> str:
    """Citation numbers after a closing quote, whether raised or typed as 13."""
    text = re.sub(rf"(?<=[.\,;:!?\"'”’)\]}}])[{_SUPER_DIGITS}]+", "", text)
    text = re.sub(rf"[{_SUPER_DIGITS}]{{2,}}", "", text)
    text = re.sub(r"(?<=[A-Za-z0-9.!?])([”\"’»])\s*\[?\d{1,3}\]?(?!\d)", r"\1", text)
    return re.sub(r"(?<=[A-Za-z])([.!?])\[?\d{1,3}\]?(?!\d)", r"\1", text)


def _unwrap_drop_caps(el) -> None:
    for child in list(el):
        name = tag_name(child)
        classes = child.get("class") or ""
        if name == "span" and (DROP_CAP_CLASS.search(classes) or _single_letter_span(el, child)):
            replace_with_text(child, collapse(visible_text(child)))
        elif (
            name == "img"
            and len((child.get("alt") or "").strip()) == 1
            and (DROP_CAP_CLASS.search(classes) or child is el[0])
        ):
            replace_with_text(child, (child.get("alt") or "").strip())


def _single_letter_span(parent, span) -> bool:
    if not len(parent) or parent[0] is not span:
        return False
    text = collapse(visible_text(span))
    if len(text) != 1 or not text.isalpha():
        return False
    tail = (span.tail or "").lstrip()
    return bool(tail[:1].islower())


def _image_to_paragraph(el) -> None:
    alt = collapse(el.get("alt") or "")
    parent = el.getparent()
    if parent is None:
        return
    if not alt:
        replace_with_text(el, "")
        return
    paragraph = el.makeelement("p")
    paragraph.text = alt
    paragraph.tail = el.tail
    parent.replace(el, paragraph)


def _drop_empty(body) -> None:
    for el in reversed(list(body.iter())):
        if el is body:
            continue
        name = tag_name(el)
        if name == "img":
            replace_with_text(el, "")
            continue
        if name in {"br", "hr"}:
            continue
        if not collapse(visible_text(el)):
            replace_with_text(el, "")


def _preview_image(el):
    alt = collapse(el.get("alt") or "")
    label = f"[image: {alt}]" if alt else "[image]"
    parent = el.getparent()
    if parent is None:
        return el
    span = el.makeelement("span")
    span.text = label
    mark_id = el.get("data-epubclean-id")
    if mark_id:
        span.set("data-epubclean-id", mark_id)
        span.set("data-epubclean-kind", el.get("data-epubclean-kind") or "image")
    span.tail = el.tail
    parent.replace(el, span)
    return span


def _inner_html(el) -> str:
    parts: list[str] = []
    if el.text:
        parts.append(escape(el.text))
    for child in el:
        if isinstance(getattr(child, "tag", None), str):
            parts.append(_element_html(child))
        if child.tail:
            parts.append(escape(child.tail))
    return "".join(parts)


def _element_html(el) -> str:
    name = tag_name(el)
    if name in {"script", "style"}:
        return ""
    if name not in SAFE_TAGS:
        if not el.get("data-epubclean-id"):
            return _inner_html(el)
        name = "span"  # a marked link or similar keeps its mark so it can still be toggled
    attrs: list[str] = []
    for key, value in el.attrib.items():
        if not isinstance(key, str) or "}" in key or ":" in key:
            continue
        local = key.lower()
        if local in SAFE_ATTRS:
            attrs.append(f' {local}="{escape(str(value), quote=True)}"')
    attr = "".join(attrs)
    if name in VOID_PREVIEW:
        return f"<{name}{attr}>"
    return f"<{name}{attr}>{_inner_html(el)}</{name}>"
