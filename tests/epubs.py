"""Build tiny EPUB files in memory for the cleaner tests."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"


def xhtml(body: str, body_type: str | None = None) -> str:
    attr = f' epub:type="{body_type}"' if body_type else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en">
<head><title>t</title></head>
<body{attr}>
{body}
</body>
</html>
"""


def make_epub(
    path: Path,
    documents: list[tuple[str, str]],
    *,
    title: str = "Sample",
    author: str = "Ada Writer",
    language: str = "en",
    spine: list[str] | None = None,
    encrypted: bool = False,
    pre_paginated: bool = False,
    landmarks: list[tuple[str, str]] | None = None,
    extra_files: dict[str, bytes] | None = None,
    metadata_extra: str = "",
    cover_image: str | None = None,
) -> Path:
    spine = spine if spine is not None else [href for href, _content in documents]
    nav = _nav(documents, landmarks or [])
    opf = _opf(
        documents,
        spine,
        title,
        author,
        language,
        pre_paginated,
        metadata_extra,
        cover_image,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""".encode(),
        )
        if encrypted:
            archive.writestr("META-INF/encryption.xml", b"<encryption/>")
        archive.writestr("OEBPS/content.opf", opf.encode())
        archive.writestr("OEBPS/nav.xhtml", nav.encode())
        for href, content in documents:
            archive.writestr(f"OEBPS/{href}", content.encode())
        for href, content in (extra_files or {}).items():
            archive.writestr(f"OEBPS/{href}", content)
    return path


def _opf(
    documents, spine, title, author, language, pre_paginated, metadata_extra, cover_image
) -> str:
    ids = {href: f"doc{index}" for index, (href, _content) in enumerate(documents)}
    items = [
        f'<item id="{ids[href]}" href="{href}" media-type="application/xhtml+xml"/>'
        for href, _content in documents
    ]
    items.append(
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    )
    if cover_image:
        items.append(
            f'<item id="cover-image" href="{cover_image}" media-type="image/png"'
            ' properties="cover-image"/>'
        )
    refs = "".join(f'<itemref idref="{ids[href]}"/>' for href in spine)
    layout = '<meta property="rendition:layout">pre-paginated</meta>' if pre_paginated else ""
    cover_meta = '<meta name="cover" content="cover-image"/>' if cover_image else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="{OPF}" unique-identifier="uid" version="3.0" xml:lang="{language}">
  <metadata xmlns:dc="{DC}">
    <dc:identifier id="uid">urn:uuid:test</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>{language}</dc:language>
    <dc:publisher>Test House</dc:publisher>
    <dc:description>A road.</dc:description>
    {metadata_extra}
    {cover_meta}
    {layout}
  </metadata>
  <manifest>
    {"".join(items)}
  </manifest>
  <spine>{refs}</spine>
</package>
"""


def _nav(documents, landmarks: list[tuple[str, str]]) -> str:
    toc = []
    for href, content in documents:
        match = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.DOTALL)
        label = re.sub(r"<[^>]+>", "", match.group(1)).strip() if match else href
        toc.append(f'<li><a href="{href}">{label}</a></li>')
    marks = "".join(
        f'<li><a epub:type="{kind}" href="{href}">{kind}</a></li>' for href, kind in landmarks
    )
    landmark_nav = f'<nav epub:type="landmarks"><ol>{marks}</ol></nav>' if marks else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
  <nav epub:type="toc"><ol>{"".join(toc)}</ol></nav>
  {landmark_nav}
</body>
</html>
"""


def semantic_book(path: Path) -> Path:
    chapter = """
<h1>The Road</h1>
<p class="header">RUNNING</p>
<p>She walked in.<a epub:type="noteref" href="#n1">1</a></p>
<aside epub:type="footnote" id="n1"><p>A note.</p></aside>
<figure>
  <img alt="A map of the road" src="map.jpg"/>
  <figcaption>The valley.</figcaption>
</figure>
<p class="pagenum">47</p>
"""
    documents = [
        (
            "text/cover.xhtml",
            xhtml("<h1>Sample</h1><img alt='Cover' src='../images/cover.png'/>", "cover"),
        ),
        ("text/title.xhtml", xhtml("<h1>Sample</h1><p>Ada Writer</p>", "titlepage")),
        ("text/copyright.xhtml", xhtml("<p>All rights reserved.</p>", "copyright-page")),
        (
            "text/toc.xhtml",
            xhtml(
                "<h1>Contents</h1><nav><ol>"
                "<li><a href='prologue.xhtml'>Prologue</a></li>"
                "<li><a href='chapter.xhtml'>The Road</a></li>"
                "<li><a href='after.xhtml'>Afterword</a></li>"
                "<li><a href='preface.xhtml'>Preface</a></li>"
                "<li><a href='intro.xhtml'>Introduction</a></li>"
                "</ol></nav>",
                "toc",
            ),
        ),
        ("text/dedication.xhtml", xhtml("<h1>Dedication</h1><p>For you.</p>", "dedication")),
        ("text/preface.xhtml", xhtml("<h1>Preface</h1><p>Read this first.</p>")),
        ("text/intro.xhtml", xhtml("<h1>Introduction</h1><p>Context matters.</p>", "introduction")),
        ("text/prologue.xhtml", xhtml("<h1>Prologue</h1><p>Before the road.</p>", "prologue")),
        ("text/chapter.xhtml", xhtml(chapter, "bodymatter")),
        ("text/after.xhtml", xhtml("<h1>Afterword</h1><p>Later on.</p>", "afterword")),
    ]
    return make_epub(
        path,
        documents,
        title="Sample",
        extra_files={"images/cover.png": b"png-cover-bytes"},
        cover_image="images/cover.png",
    )


def pdf_book(path: Path) -> Path:
    def chapter(heading: str, sentence: str, page: str) -> str:
        return xhtml(f"<p>BOOK TITLE</p><h1>{heading}</h1><p>{sentence}</p><p>{page}</p>")

    documents = [
        ("text/c1.xhtml", chapter("Chapter One", "Alpha text here.", "14")),
        ("text/c2.xhtml", chapter("Chapter Two", "Beta text here.", "15")),
        ("text/c3.xhtml", chapter("Chapter Three", "Gamma text here.", "16")),
    ]
    return make_epub(path, documents, title="Gazette")


def hyphen_book(path: Path) -> Path:
    body = (
        "<h1>Words</h1>"
        '<p><span class="dropcap">T</span>he night.</p>'
        "<p>Say hel\u00adlo now.</p>"
        "<p>A hel-\nlo there.</p>"
        "<p>Meet Jean-\nLuc today.</p>"
    )
    return make_epub(path, [("text/words.xhtml", xhtml(body))], title="Words")
