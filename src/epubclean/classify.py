"""Decide what each chapter is, and which blocks inside it are furniture."""

from __future__ import annotations

import re

from epubclean.model import Book, Chapter, Mark
from epubclean.xmlutil import collapse, epub_types, find_body, tag_name, visible_text

TYPE_TO_ROLE = {
    "cover": "cover",
    "titlepage": "titlepage",
    "title-page": "titlepage",
    "copyright-page": "copyright",
    "copyright": "copyright",
    "colophon": "colophon",
    "toc": "toc",
    "landmarks": "toc",
    "page-list": "toc",
    "loi": "toc",
    "lot": "toc",
    "dedication": "dedication",
    "preface": "preface",
    "foreword": "foreword",
    "introduction": "introduction",
    "intro": "introduction",
    "acknowledgments": "acknowledgments",
    "acknowledgements": "acknowledgments",
    "index": "index",
    "glossary": "glossary",
    "bibliography": "bibliography",
    "biblio": "bibliography",
    "afterword": "afterword",
    "prologue": "prologue",
    "epilogue": "epilogue",
    "endnotes": "notes",
    "footnotes": "notes",
    "rearnotes": "notes",
    "bodymatter": "body",
    "chapter": "body",
}

ROLE_PRIORITY = (
    "cover",
    "titlepage",
    "copyright",
    "colophon",
    "toc",
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
    "prologue",
    "epilogue",
    "body",
)

GUIDE_TO_ROLE = {
    "cover": "cover",
    "title-page": "titlepage",
    "copyright-page": "copyright",
    "toc": "toc",
    "dedication": "dedication",
    "preface": "preface",
    "foreword": "foreword",
    "introduction": "introduction",
    "acknowledgments": "acknowledgments",
    "acknowledgements": "acknowledgments",
    "bibliography": "bibliography",
    "index": "index",
    "glossary": "glossary",
    "epilogue": "epilogue",
    "prologue": "prologue",
    "notes": "notes",
    "colophon": "colophon",
    "loi": "toc",
    "lot": "toc",
}

HEADING_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(the )?(table of )?contents$"), "toc"),
    (re.compile(r"^copyrights?( page)?$"), "copyright"),
    (re.compile(r"^title pages?$"), "titlepage"),
    (re.compile(r"^dedications?$"), "dedication"),
    (re.compile(r"^prefaces?$"), "preface"),
    (re.compile(r"^forewords?$"), "foreword"),
    (re.compile(r"^introductions?$"), "introduction"),
    (re.compile(r"^acknowledge?ments$"), "acknowledgments"),
    (re.compile(r"^also by\b"), "also-by"),
    (re.compile(r"^about the authors?$"), "about-author"),
    (re.compile(r"^index$"), "index"),
    (re.compile(r"^glossary$"), "glossary"),
    (re.compile(r"^bibliograph(y|ies)$"), "bibliography"),
    (re.compile(r"^afterwords?$"), "afterword"),
    (re.compile(r"^prologues?$"), "prologue"),
    (re.compile(r"^epilogues?$"), "epilogue"),
    (re.compile(r"^colophons?$"), "colophon"),
    (re.compile(r"^(end ?)?notes$"), "notes"),
    (re.compile(r"^footnotes$"), "notes"),
)

FILENAME_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(^|[^a-z])cover([^a-z]|$)"), "cover"),
    (re.compile(r"titlepage|title-page|title_page"), "titlepage"),
    (re.compile(r"(^|[^a-z])copyright"), "copyright"),
    (re.compile(r"(^|[^a-z])(toc|contents)([^a-z]|$)"), "toc"),
    (re.compile(r"dedicat"), "dedication"),
    (re.compile(r"preface"), "preface"),
    (re.compile(r"foreword"), "foreword"),
    (re.compile(r"(^|[^a-z])intro(duction)?([^a-z]|$)"), "introduction"),
    (re.compile(r"acknowledg"), "acknowledgments"),
    (re.compile(r"also-?by|other-?books"), "also-by"),
    (re.compile(r"about-?the-?author|about-?author"), "about-author"),
    (re.compile(r"(^|[^a-z])index([^a-z]|$)"), "index"),
    (re.compile(r"glossar"), "glossary"),
    (re.compile(r"biblio"), "bibliography"),
    (re.compile(r"afterword"), "afterword"),
    (re.compile(r"prologue"), "prologue"),
    (re.compile(r"epilogue"), "epilogue"),
    (re.compile(r"colophon"), "colophon"),
    (re.compile(r"footnote|endnote"), "notes"),
)

FURNITURE_CLASS = re.compile(
    r"(?:^|[^a-z0-9])(?:"
    r"running[-_ ]?heads?|running[-_ ]?headers?|running[-_ ]?footers?|"
    r"page[-_ ]?numbers?|page[-_ ]?nums?|pagenumbers?|pagenums?|"
    r"headers?|footers?|pagetops?|pagebottoms?"
    r")(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)
PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d{1,4}$", re.IGNORECASE)
ROMAN_PAGE = re.compile(r"^[ivxlcdm]{1,8}$", re.IGNORECASE)
NOTE_HREF = re.compile(
    r"(?:footnote|endnote|rearnote|(?:^|[#/_.-])(?:fn|note|n)[-_]?\d)",
    re.IGNORECASE,
)
HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
LEAF_TAGS = HEADING_TAGS | {"p", "pre", "figcaption", "li", "blockquote", "td", "th"}
WRAP_TAGS = {
    "div",
    "section",
    "article",
    "ul",
    "ol",
    "aside",
    "figure",
    "table",
    "header",
    "footer",
    "nav",
}
BLOCKISH = LEAF_TAGS | WRAP_TAGS


def classify_book(book: Book) -> None:
    for index, chapter in enumerate(book.chapters):
        if chapter.spine_kind == "image":
            chapter.role = "cover"
            chapter.reason = "The spine item is an image."
        else:
            _classify_role(book, chapter, index)
        _mark_blocks(chapter)
        chapter.original_chars = len(collapse(visible_text(find_body(chapter.tree))))
    _mark_repeated(book)
    for chapter in book.chapters:
        _refresh_heading(book, chapter)


def _classify_role(book: Book, chapter: Chapter, index: int) -> None:
    tokens = _document_types(chapter.tree)
    role = _role_from_types(tokens)
    if role:
        chapter.role = role
        chapter.reason = f"Marked as {role.replace('-', ' ')} in the book."
        return
    landmark = book.landmarks.get(chapter.href)
    if landmark:
        role = _role_from_types([landmark])
        if role and role != "body":
            chapter.role = role
            chapter.reason = "Listed in the landmarks nav."
            return
    guide = book.guide.get(chapter.href)
    mapped = GUIDE_TO_ROLE.get(guide or "")
    if mapped:
        chapter.role = mapped
        chapter.reason = f"Guide type is {guide}."
        return
    for label in (book.nav_labels.get(chapter.href, ""), _first_heading(chapter.tree)):
        role = _match_heading(label)
        if role:
            chapter.role = role
            chapter.reason = f"Heading is “{label.strip()}”."
            return
    role = _match_filename(chapter.href)
    if role:
        chapter.role = role
        chapter.reason = "The filename says what this file is."
        return
    if _is_link_toc(chapter.tree):
        chapter.role = "toc"
        chapter.reason = "Mostly links, like a table of contents."
        return
    if _looks_like_copyright(chapter.tree):
        chapter.role = "copyright"
        chapter.reason = "A short page that mentions rights or an ISBN."
        return
    if _looks_like_titlepage(book, chapter, index):
        chapter.role = "titlepage"
        chapter.reason = "A short opening page containing the book title."
        return
    chapter.role = "body"
    chapter.reason = "Chapter prose."


def _document_types(tree) -> list[str]:
    tokens: list[str] = []
    for el in tree.iter():
        name = tag_name(el)
        if name in {"html", "body"}:
            tokens.extend(epub_types(el))
        elif name in {"section", "article"}:
            parent = el.getparent()
            if parent is not None and tag_name(parent) in {"body", "html"}:
                tokens.extend(epub_types(el))
    return tokens


def _role_from_types(tokens: list[str]) -> str | None:
    roles = {TYPE_TO_ROLE[token] for token in tokens if token in TYPE_TO_ROLE}
    for role in ROLE_PRIORITY:
        if role in roles:
            return role
    return None


def _match_heading(text: str) -> str | None:
    label = _norm_label(text)
    if not label:
        return None
    for pattern, role in HEADING_RULES:
        if pattern.search(label):
            return role
    return None


def _norm_label(text: str) -> str:
    text = text.casefold().replace("’", "'")
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _match_filename(href: str) -> str | None:
    stem = href.rsplit("/", 1)[-1].rsplit(".", 1)[0].casefold()
    for pattern, role in FILENAME_RULES:
        if pattern.search(stem):
            return role
    return None


def _first_heading(tree) -> str:
    for el in find_body(tree).iter():
        if tag_name(el) in HEADING_TAGS:
            text = collapse(visible_text(el))
            if text:
                return text
    return ""


def _is_link_toc(tree) -> bool:
    body = find_body(tree)
    anchors = [el for el in body.iter() if tag_name(el) == "a" and (el.get("href") or "").strip()]
    if len(anchors) < 5:
        return False
    total = collapse(visible_text(body))
    linked = collapse(" ".join(collapse(visible_text(anchor)) for anchor in anchors))
    if not total:
        return True
    return len(linked) / len(total) > 0.6


def _looks_like_copyright(tree) -> bool:
    text = collapse(visible_text(find_body(tree)))
    if len(text) > 2500:
        return False
    return re.search(r"all rights reserved|\bisbn\b", text, re.IGNORECASE) is not None


def _looks_like_titlepage(book: Book, chapter: Chapter, index: int) -> bool:
    """A short opening file whose heading is the book title, not a chapter of sentences."""
    if index > 2 or not book.title:
        return False
    heading = _first_heading(chapter.tree)
    if not heading or _norm_label(book.title) not in _norm_label(heading):
        return False
    text = collapse(visible_text(find_body(chapter.tree)))
    remainder = collapse(text.casefold().replace(heading.casefold(), "", 1))
    if not text or len(remainder) > 180 or len(remainder.split()) > 12:
        return False
    # Two sentence endings means this is prose that happens to share the book's name.
    return len(re.findall(r"[.!?]", remainder)) < 2


def _mark_blocks(chapter: Chapter) -> None:
    body = find_body(chapter.tree)
    chapter.marks = []
    counter = 0

    def inside_junk(el) -> bool:
        parent = el.getparent()
        while parent is not None:
            kind = parent.get("data-epubclean-kind")
            if kind and kind != "prose":
                return True
            parent = parent.getparent()
        return False

    def mark(el, kind: str, reason: str) -> None:
        nonlocal counter
        mark_id = f"{chapter.id}-b{counter}"
        counter += 1
        el.set("data-epubclean-id", mark_id)
        el.set("data-epubclean-kind", kind)
        chapter.marks.append(Mark(mark_id, kind, reason, collapse(visible_text(el))[:160]))

    for el in body.iter():
        if not isinstance(getattr(el, "tag", None), str) or el is body:
            continue
        if inside_junk(el):
            continue
        name = tag_name(el)
        if name in {"script", "style"}:
            continue
        classified = _classify_element(el, name)
        if classified:
            mark(el, *classified)
            continue
        if name in LEAF_TAGS | {"div", "section", "article"} and not _has_block_child(el):
            text = collapse(visible_text(el))
            if text:
                mark(el, "prose", "")
    _lift_glued_citations(body, mark)


# beliefs.”13, or a note glued to the stop: significance.2
_GLUED_CITATION = re.compile(r"(?<=[A-Za-z0-9.!?])([”\"’»])(\s*)(\[?\d{1,3}\]?)(?!\d)")
_GLUED_STOP = re.compile(r"(?<=[A-Za-z])[.!?](\[?\d{1,3}\]?)(?!\d)")


def _citation_bounds(text: str) -> tuple[int, int] | None:
    """Start and end of a citation number, leaving the punctuation in front of it."""
    quote = _GLUED_CITATION.search(text)
    stop = _GLUED_STOP.search(text)
    found = [item for item in (quote, stop) if item is not None]
    if not found:
        return None
    match = min(found, key=lambda item: item.start())
    if match is quote:
        return match.start(3), match.end(3)
    return match.start(1), match.end(1)


def _lift_glued_citations(body, mark) -> None:
    """Turn a trailing 13 after a closing quote into a reference we can drop."""

    def remember(span) -> None:
        mark(span, "reference", "Reference number.")

    for el in list(body.iter()):
        if not isinstance(getattr(el, "tag", None), str):
            continue
        kind = el.get("data-epubclean-kind")
        if kind and kind != "prose":
            continue
        if el.text:
            el.text = _consume_citations(el, el.text, 0, remember)
        # lxml's len(element) and indexed child access walk the sibling list.
        # Repeating them for a wide element makes this loop quadratic.  Follow
        # sibling links instead; this also visits reference spans inserted by
        # _consume_tail, so multiple citations in one tail still work.
        child = next(iter(el), None)
        while child is not None:
            if child.tail:
                child.tail = _consume_tail(child, remember)
            child = child.getnext()


def _consume_citations(parent, text: str, index: int, remember) -> str:
    bounds = _citation_bounds(text)
    if bounds is None:
        return text
    start, end = bounds
    span = parent.makeelement("span")
    span.text = text[start:end]
    span.tail = text[end:]
    remember(span)
    parent.insert(index, span)
    return text[:start]


def _consume_tail(child, remember) -> str:
    text = child.tail or ""
    bounds = _citation_bounds(text)
    if bounds is None:
        return text
    start, end = bounds
    span = child.makeelement("span")
    span.text = text[start:end]
    span.tail = text[end:]
    remember(span)
    child.addnext(span)
    return text[:start]


def _has_block_child(el) -> bool:
    return any(tag_name(child) in BLOCKISH for child in el)


def _classify_element(el, name: str) -> tuple[str, str] | None:
    types = set(epub_types(el))
    text = collapse(visible_text(el))
    short = len(text) <= 80
    blob = f"{el.get('class') or ''} {el.get('id') or ''}"
    classed = FURNITURE_CLASS.search(blob) is not None

    if "pagebreak" in types or "page-break" in types:
        return "pagebreak", "Page break."
    if types & {"footnote", "rearnote", "endnote"} and name not in HEADING_TAGS:
        return "footnote", "Footnote."
    if "noteref" in types or "rearnoteref" in types:
        return "noteref", "Footnote marker."
    if name == "figcaption":
        return "caption", "Image caption."
    if name == "img":
        alt = (el.get("alt") or "").strip()
        if len(alt) == 1 and _leading_drop_cap(el):
            return None
        return "image", "Image."
    if _is_superscript_reference(el, name, text):
        return "reference", "Reference number."
    if _is_noteref_anchor(el, name):
        return "noteref", "Footnote marker."
    if classed and short and name not in HEADING_TAGS and not _has_block_child(el):
        if PAGE_NUMBER.match(text) or ROMAN_PAGE.match(text) or "page" in blob.casefold():
            return "page-number", "Page number."
        if re.search(r"footer", blob, re.IGNORECASE):
            return "footer", "Running footer."
        return "header", "Running header."
    if (
        name in {"p", "div", "span", "li"}
        and text
        and PAGE_NUMBER.match(text)
        and name not in HEADING_TAGS
    ):
        return "page-number", "Page number."
    if name == "footer" and short and text and not _has_block_child(el):
        return "footer", "Footer."
    return None


def _leading_drop_cap(el) -> bool:
    parent = el.getparent()
    if parent is None or tag_name(parent) != "p":
        return False
    classes = el.get("class") or ""
    if re.search(r"drop[-_]?cap|first[-_]?letter", classes, re.IGNORECASE):
        return True
    return parent[0] is el if len(parent) else False


REFERENCE_TEXT = re.compile(
    r"^(?:[\[\(])?(?:\d{1,4}|[ivxlcdm]{1,6}|\*+|†+|‡+|§+|¶+)"
    r"(?:\s*[,;–—-]\s*(?:[\[\(])?(?:\d{1,4}|[ivxlcdm]{1,6})(?:[\]\)])?)*"
    r"(?:[\]\)])?$",
    re.IGNORECASE,
)
SUPERSCRIPT_CLASS = re.compile(
    r"(?:^|[^a-z])(?:sup|superscript|super|noteref|footnote(?:-?ref)?|endnote(?:-?ref)?)(?:[^a-z]|$)",
    re.IGNORECASE,
)


def _is_superscript_reference(el, name: str, text: str) -> bool:
    """A raised citation number, like the 11 after a quotation."""
    if not text or len(text) > 20 or REFERENCE_TEXT.match(text) is None:
        return False
    if not _looks_raised(el, name):
        return False
    if _note_signal(el):
        return True
    before = _text_before(el).rstrip()
    if before and before[-1] in ".,;:!?\"'”’)\\]}":
        return True
    if re.search(r"\d{2,}|[\[\(]|[*†‡§¶]", text):
        return True
    # A single digit after one letter or a number is an exponent: m², 10⁶.
    if before and re.search(r"(?:^|\s)(?:[A-Za-z]|\d+)$", before) and re.fullmatch(r"\d", text):
        return False
    return True


def _looks_raised(el, name: str) -> bool:
    if name == "sup":
        return True
    blob = f"{el.get('class') or ''} {el.get('id') or ''}"
    style = el.get("style") or ""
    if SUPERSCRIPT_CLASS.search(blob) or re.search(r"vertical-align\s*:\s*super", style, re.I):
        return True
    if name == "a":
        parent = el.getparent()
        if parent is not None and tag_name(parent) == "sup":
            return True
        return any(tag_name(child) == "sup" for child in el.iter())
    return False


def _note_signal(el) -> bool:
    blob = f"{el.get('class') or ''} {el.get('id') or ''} {el.get('href') or ''}"
    if re.search(r"footnote|endnote|noteref", blob, re.I):
        return True
    if set(epub_types(el)) & {"noteref", "rearnoteref"}:
        return True
    return any(
        set(epub_types(child)) & {"noteref", "rearnoteref"}
        or re.search(r"footnote|endnote|noteref", child.get("href") or "", re.I)
        for child in el.iter()
        if child is not el
    )


def _text_before(el) -> str:
    previous = el.getprevious()
    if previous is not None:
        return visible_text(previous) + (previous.tail or "")
    parent = el.getparent()
    if parent is None:
        return ""
    return parent.text or ""


def _is_noteref_anchor(el, name: str) -> bool:
    if name != "a":
        return False
    if set(epub_types(el)) & {"noteref", "rearnoteref"}:
        return True
    href = el.get("href") or ""
    text = collapse(visible_text(el))
    if len(text) > 6:
        return False
    parent = el.getparent()
    parent_name = tag_name(parent) if parent is not None else ""
    if parent_name == "sup" and href.startswith("#"):
        return True
    return bool(href) and NOTE_HREF.search(href) is not None


def _mark_repeated(book: Book) -> None:
    total = len(book.chapters)
    if total < 2:
        return
    groups: dict[str, list[tuple[Chapter, Mark]]] = {}
    for chapter in book.chapters:
        ordered = _ordered_marks(chapter)
        edge_ids = {mark.id for _, mark in ordered[:3] + ordered[-3:]}
        for el, mark in ordered:
            if mark.kind in {
                "footnote",
                "noteref",
                "reference",
                "caption",
                "image",
                "pagebreak",
                "page-number",
            }:
                continue
            text = collapse(visible_text(el))
            if len(text) < 2 or len(text) > 80:
                continue
            classed = FURNITURE_CLASS.search(f"{el.get('class') or ''} {el.get('id') or ''}")
            if mark.id not in edge_ids and not classed and tag_name(el) not in {"header", "footer"}:
                continue
            groups.setdefault(text.casefold(), []).append((chapter, mark))
    for hits in groups.values():
        chapter_ids = {chapter.id for chapter, _ in hits}
        if len(chapter_ids) < 2 or len(chapter_ids) * 2 < total:
            continue
        reason = f"Repeated short line on {len(chapter_ids)} chapters."
        for _chapter, mark in hits:
            if mark.kind == "prose":
                mark.kind = "header"
                mark.reason = reason
        for chapter, _mark in hits:
            _sync_kind_attributes(chapter)


def _ordered_marks(chapter: Chapter) -> list[tuple[object, Mark]]:
    by_id = {mark.id: mark for mark in chapter.marks}
    ordered = []
    for el in find_body(chapter.tree).iter():
        mark = by_id.get(el.get("data-epubclean-id") or "")
        if mark is not None:
            ordered.append((el, mark))
    return ordered


def _sync_kind_attributes(chapter: Chapter) -> None:
    by_id = {mark.id: mark for mark in chapter.marks}
    for el in find_body(chapter.tree).iter():
        mark = by_id.get(el.get("data-epubclean-id") or "")
        if mark is not None:
            el.set("data-epubclean-kind", mark.kind)


def _refresh_heading(book: Book, chapter: Chapter) -> None:
    by_id = {mark.id: mark for mark in chapter.marks}
    for el in find_body(chapter.tree).iter():
        if tag_name(el) not in HEADING_TAGS:
            continue
        mark = by_id.get(el.get("data-epubclean-id") or "")
        if mark is None or mark.kind != "prose":
            continue
        text = collapse(visible_text(el))
        if text:
            chapter.heading = text
            return
    if chapter.heading and not _heading_reclassified(chapter, chapter.heading):
        return
    for mark in chapter.marks:
        if mark.kind == "prose" and mark.text:
            chapter.heading = mark.text[:120]
            return
    label = book.nav_labels.get(chapter.href)
    if label:
        chapter.heading = label
    elif not chapter.heading:
        chapter.heading = "Untitled"


def _heading_reclassified(chapter: Chapter, heading: str) -> bool:
    folded = heading.casefold()
    return any(mark.kind != "prose" and mark.text.casefold() == folded for mark in chapter.marks)
