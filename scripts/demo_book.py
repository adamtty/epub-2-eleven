"""Write a small demo EPUB, full of the furniture epub-2-eleven strips, for trying it out.

The text is the opening of Pride and Prejudice (public domain). The copyright page, contents,
running heads, page numbers and notes are made up, the way a real ebook would have them.

    python scripts/demo_book.py demo.epub
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

HEAD = "PRIDE AND PREJUDICE"

CHAPTER_ONE = [
    "It is a truth universally acknowledged, that a single man in possession of a good fortune,"
    " must be in want of a wife.",
    "However little known the feelings or views of such a man may be on his first entering a"
    " neighbourhood, this truth is so well fixed in the minds of the surrounding families, that"
    " he is considered the rightful property of some one or other of their daughters.",
    "“My dear Mr. Bennet,” said his lady to him one day, “have you heard that"
    " Netherfield Park is let at last?”<a epub:type='noteref' href='notes.xhtml#n1'>1</a>",
    "Mr. Bennet replied that he had not.",
    "“But it is,” returned she; “for Mrs. Long has just been here, and she told me all about it.”",
    "Mr. Bennet made no answer.",
    "“Do you not want to know who has taken it?” cried his wife impatiently.",
    "“You want to tell me, and I have no objection to hearing it.”",
    "This was invitation enough.",
]

CHAPTER_TWO = [
    "Mr. Bennet was among the earliest of those who waited on Mr. Bingley. He had always intended"
    " to visit him, though to the last always assuring his wife that he should not go; and till"
    " the evening after the visit was paid she had no knowledge of it.",
    "It was then disclosed in the following manner. Observing his second daughter employed in"
    " trimming a hat, he suddenly addressed her with,",
    "“I hope Mr. Bingley will like it, Lizzy.”",
    "“We are not in a way to know what Mr. Bingley likes,” said her mother"
    " resentfully, “since we are not to visit.”",
]


def page(body: str, kind: str = "", title: str = "Pride and Prejudice") -> str:
    attr = f' epub:type="{kind}"' if kind else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en">
<head><title>{title}</title></head>
<body{attr}>
{body}
</body>
</html>
"""


def chapter(heading: str, paragraphs: list[str], first_page: int) -> str:
    parts = [f'<p class="running-head">{HEAD}</p>', f"<h1>{heading}</h1>"]
    for index, text in enumerate(paragraphs):
        if index == 0:
            text = f'<span class="dropcap">{text[0]}</span>{text[1:]}'
        parts.append(f"<p>{text}</p>")
        if index == len(paragraphs) // 2:
            parts.append(f'<p class="pagenum">{first_page}</p>')
            parts.append(f'<p class="running-head">{HEAD}</p>')
    parts.append(f'<p class="pagenum">{first_page + 1}</p>')
    return page("\n".join(parts), "bodymatter chapter")


DOCUMENTS = [
    ("cover.xhtml", page("<h1>Pride and Prejudice</h1><p>Jane Austen</p>", "cover")),
    (
        "title.xhtml",
        page("<h1>Pride and Prejudice</h1><p>Jane Austen</p><p>Demo Press</p>", "titlepage"),
    ),
    (
        "copyright.xhtml",
        page(
            "<p>Demo edition. Text in the public domain.</p>"
            "<p>All rights reserved in this made-up arrangement.</p><p>ISBN 000-0-00-000000-0</p>",
            "copyright-page",
        ),
    ),
    (
        "contents.xhtml",
        page(
            "<h1>Contents</h1><ol><li><a href='chapter-1.xhtml'>Chapter 1</a></li>"
            "<li><a href='chapter-2.xhtml'>Chapter 2</a></li>"
            "<li><a href='notes.xhtml'>Notes</a></li></ol>",
            "toc",
        ),
    ),
    ("dedication.xhtml", page("<h1>Dedication</h1><p>For every patient listener.</p>")),
    ("chapter-1.xhtml", chapter("Chapter 1", CHAPTER_ONE, 1)),
    ("chapter-2.xhtml", chapter("Chapter 2", CHAPTER_TWO, 3)),
    (
        "notes.xhtml",
        page(
            "<h1>Notes</h1><aside epub:type='endnote' id='n1'><p>1. Netherfield Park: the"
            " house nearest Longbourn, let to Mr. Bingley.</p></aside>",
            "backmatter endnotes",
        ),
    ),
    (
        "about.xhtml",
        page("<h1>About the Author</h1><p>Jane Austen (1775–1817) wrote six novels.</p>"),
    ),
]


def write(path: Path) -> Path:
    ids = {href: f"d{index}" for index, (href, _body) in enumerate(DOCUMENTS)}
    items = "".join(
        f'<item id="{ids[href]}" href="{href}" media-type="application/xhtml+xml"/>'
        for href, _body in DOCUMENTS
    )
    spine = "".join(f'<itemref idref="{ids[href]}"/>' for href, _body in DOCUMENTS)
    toc = "".join(
        f'<li><a href="{href}">{label}</a></li>'
        for href, label in [
            ("chapter-1.xhtml", "Chapter 1"),
            ("chapter-2.xhtml", "Chapter 2"),
            ("notes.xhtml", "Notes"),
        ]
    )
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="en">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">urn:uuid:00000000-0000-4000-8000-000000000000</dc:identifier>
<dc:title>Pride and Prejudice</dc:title><dc:creator>Jane Austen</dc:creator>
<dc:language>en</dc:language><meta property="dcterms:modified">2020-01-01T00:00:00Z</meta>
</metadata>
<manifest>{items}<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"
 properties="nav"/></manifest>
<spine>{spine}</spine>
</package>"""
    nav = page(f'<nav epub:type="toc"><ol>{toc}</ol></nav>', title="Contents")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0"'
            ' xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/nav.xhtml", nav)
        for href, body in DOCUMENTS:
            archive.writestr(f"OEBPS/{href}", body, compress_type=zipfile.ZIP_DEFLATED)
    return path


if __name__ == "__main__":
    print(write(Path(sys.argv[1] if len(sys.argv) > 1 else "demo.epub")))
