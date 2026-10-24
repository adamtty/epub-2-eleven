"""Books, chapters, and the errors the cleaner shows to a person."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class EpubError(Exception):
    """A book this tool will not clean, with a message safe to show."""


class DRMError(EpubError):
    pass


class FixedLayoutError(EpubError):
    pass


@dataclass
class Mark:
    """One toggleable piece of a chapter: a paragraph, a page number, a note."""

    id: str
    kind: str
    reason: str
    text: str


@dataclass
class Chapter:
    id: str
    href: str
    heading: str
    role: str
    reason: str
    tree: Any
    spine_kind: str = "xhtml"
    marks: list[Mark] = field(default_factory=list)
    original_chars: int = 0


@dataclass
class Book:
    source_path: Path
    title: str
    author: str
    language: str
    chapters: list[Chapter]
    guide: dict[str, str] = field(default_factory=dict)
    landmarks: dict[str, str] = field(default_factory=dict)
    nav_labels: dict[str, str] = field(default_factory=dict)
    from_upload: bool = False
    export_stem: str = ""
    unique_identifier: str = "uid"
    metadata_xml: bytes = b""
    cover_media: str = ""
    cover_bytes: bytes = b""
