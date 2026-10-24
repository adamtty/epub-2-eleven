"""Which roles and block kinds each preset leaves out."""

from __future__ import annotations

PRESETS = ("narration", "conservative")

# Cover, title, copyright, contents, and publisher colophon.
STRUCTURAL_ROLES = frozenset({"cover", "titlepage", "copyright", "toc", "colophon"})

# Narration also skips matter around the book. Prologue and epilogue stay.
NARRATION_ROLES = STRUCTURAL_ROLES | frozenset(
    {
        "dedication",
        "preface",
        "foreword",
        "introduction",
        "acknowledgments",
        "also-by",
        "about-author",
        "index",
        "glossary",
        "bibliography",
        "afterword",
        "notes",
    }
)

STRUCTURAL_KINDS = frozenset({"page-number", "header", "footer", "pagebreak", "reference"})

NARRATION_KINDS = STRUCTURAL_KINDS | frozenset({"footnote", "noteref", "caption", "image"})


def chapter_drop(role: str, preset: str) -> bool:
    roles = NARRATION_ROLES if preset == "narration" else STRUCTURAL_ROLES
    return role in roles


def block_drop(kind: str, preset: str) -> bool:
    if kind == "prose":
        return False
    kinds = NARRATION_KINDS if preset == "narration" else STRUCTURAL_KINDS
    return kind in kinds


def is_included(item_id: str, suggested_drop: bool, overrides: dict[str, bool]) -> bool:
    if item_id in overrides:
        return bool(overrides[item_id])
    return not suggested_drop
