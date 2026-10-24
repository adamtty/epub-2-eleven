"""Namespace-tolerant helpers for EPUB XML and XHTML."""

from __future__ import annotations

import re
from typing import Any

SOFT_HYPHENS = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\ufeff"), None)
VOID_TAGS = frozenset({"br", "hr", "img", "meta", "link"})


def tag_name(el: Any) -> str:
    tag = getattr(el, "tag", None)
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        tag = tag.rsplit("}", 1)[-1]
    if ":" in tag:
        tag = tag.split(":", 1)[-1]
    return tag.lower()


def attr_local(name: str) -> str:
    if "}" in name:
        name = name.rsplit("}", 1)[-1]
    if ":" in name:
        name = name.split(":", 1)[-1]
    return name.lower()


def epub_types(el: Any) -> list[str]:
    tokens: list[str] = []
    for key, val in el.attrib.items():
        key_l = key.lower()
        if attr_local(key) != "type":
            continue
        if "epub" in key_l or "idpf.org/2007/ops" in key_l:
            tokens.extend(str(val).split())
    return [token.lower() for token in tokens]


def find_body(tree: Any) -> Any:
    for el in tree.iter():
        if tag_name(el) == "body":
            return el
    return tree


def visible_text(el: Any) -> str:
    """Text a reader would see, skipping scripts and styles."""
    chunks: list[str] = []

    def walk(node: Any) -> None:
        if not isinstance(getattr(node, "tag", None), str):
            return
        if tag_name(node) in {"script", "style"}:
            return
        if node.text:
            chunks.append(node.text)
        for child in node:
            if isinstance(getattr(child, "tag", None), str):
                walk(child)
            if child.tail:
                chunks.append(child.tail)

    walk(el)
    return "".join(chunks)


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def replace_with_text(el: Any, text: str) -> None:
    """Remove an element and leave its text, including the words that followed it."""
    parent = el.getparent()
    if parent is None:
        return
    prev = el.getprevious()
    tail = el.tail or ""
    if prev is not None:
        prev.tail = (prev.tail or "") + text + tail
    else:
        parent.text = (parent.text or "") + text + tail
    parent.remove(el)


def strip_invisible(el: Any) -> None:
    if el.text:
        el.text = el.text.translate(SOFT_HYPHENS)
    for child in el:
        if isinstance(getattr(child, "tag", None), str):
            strip_invisible(child)
        if child.tail:
            child.tail = child.tail.translate(SOFT_HYPHENS)
