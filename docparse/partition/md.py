"""Markdown partitioner: a small line-oriented block parser (CommonMark subset).

Handles ATX (``#``) and setext (``===``/``---``) headings, fenced and indented code,
bullet/ordered lists with nesting, GFM pipe tables, block quotes, images, and
paragraphs. Inline links/emphasis are recorded as metadata and stripped from text.
"""

from __future__ import annotations

import re
from typing import Optional

from docparse.cleaners import clean_extra_whitespace
from docparse.elements import CodeSnippet, Element, ElementMetadata, Image, ListItem, Table, Title
from docparse.partition.common import FileLike, base_metadata, classify_text, decode_text, finalize, read_bytes
from docparse.partition.html import rows_to_html, rows_to_text

ATX_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)\s*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
LIST_RE = re.compile(r"^(\s*)(?:[-*+]|\d{1,9}[.)])\s+(.*)$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
HR_RE = re.compile(r"^ {0,3}([-*_])(\s*\1){2,}\s*$")
IMAGE_RE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\s]+)[^)]*\)\s*$")
LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)[^)]*\)")
EMPH_RE = re.compile(r"(\*\*|__)(.+?)\1|(?<![\w*])([*_])(?!\s)(.+?)(?<!\s)\3(?![\w*])")
CODE_SPAN_RE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> tuple[str, dict]:
    meta: dict = {}
    links = LINK_RE.findall(text)
    if links:
        meta["link_texts"] = [t for t, _ in links]
        meta["link_urls"] = [u for _, u in links]
    text = LINK_RE.sub(r"\1", text)
    emph_text, emph_tags = [], []
    for m in EMPH_RE.finditer(text):
        if m.group(2):
            emph_text.append(m.group(2)); emph_tags.append("b")
        else:
            emph_text.append(m.group(4)); emph_tags.append("i")
    if emph_text:
        meta["emphasized_text_contents"] = emph_text
        meta["emphasized_text_tags"] = emph_tags
    text = EMPH_RE.sub(lambda m: m.group(2) or m.group(4), text)
    text = CODE_SPAN_RE.sub(r"\1", text)
    return clean_extra_whitespace(text), meta


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [clean_extra_whitespace(c.replace("\\|", "|")) for c in re.split(r"(?<!\\)\|", line)]


def markdown_to_elements(text: str, base_md: Optional[ElementMetadata] = None) -> list[Element]:
    base_md = base_md or ElementMetadata()
    lines = text.replace("\r\n", "\n").replace("\t", "    ").split("\n")
    out: list[Element] = []
    para: list[str] = []

    def md(**kw) -> ElementMetadata:
        m = base_md.copy()
        for k, v in kw.items():
            setattr(m, k, v)
        return m

    def flush_para():
        if not para:
            return
        joined = " ".join(p.strip() for p in para)
        para.clear()
        if joined.startswith(">"):
            joined = re.sub(r"(^|\s)>\s?", " ", joined)
        plain, meta = _inline(joined)
        el = classify_text(plain, md(**meta))
        if el is not None:
            out.append(el)

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # fenced code
        m = FENCE_RE.match(line)
        if m:
            flush_para()
            fence = m.group(1)
            lang = m.group(2).strip() or None
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                body.append(lines[i]); i += 1
            i += 1
            code = "\n".join(body)
            if code.strip():
                out.append(CodeSnippet(code, md(style_name=lang)))
            continue

        if not stripped:
            flush_para(); i += 1; continue

        # ATX heading
        m = ATX_RE.match(line)
        if m:
            flush_para()
            plain, meta = _inline(m.group(2))
            if plain:
                out.append(Title(plain, md(category_depth=len(m.group(1)) - 1, **meta)))
            i += 1; continue

        # setext heading (paragraph followed by === or ---)
        if para and SETEXT_RE.match(line) and not LIST_RE.match(line):
            depth = 0 if line.strip()[0] == "=" else 1
            plain, meta = _inline(" ".join(p.strip() for p in para))
            para.clear()
            out.append(Title(plain, md(category_depth=depth, **meta)))
            i += 1; continue

        if HR_RE.match(line):
            flush_para(); i += 1; continue

        # table: header row + separator row
        if "|" in line and i + 1 < len(lines) and TABLE_SEP_RE.match(lines[i + 1]):
            flush_para()
            rows = [_split_row(line)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i])); i += 1
            rows = [[_inline(c)[0] for c in r] for r in rows]
            out.append(Table(rows_to_text(rows), md(text_as_html=rows_to_html(rows))))
            continue

        m = IMAGE_RE.match(line)
        if m:
            flush_para()
            out.append(Image(m.group(1) or m.group(2), md(url=m.group(2))))
            i += 1; continue

        m = LIST_RE.match(line)
        if m and (not para or m.group(1) == ""):
            flush_para()
            indent = len(m.group(1))
            item = [m.group(2)]
            i += 1
            # lazy continuation lines belong to the same item
            while i < len(lines) and lines[i].strip() and not LIST_RE.match(lines[i]) \
                    and not ATX_RE.match(lines[i]) and not FENCE_RE.match(lines[i]):
                item.append(lines[i].strip()); i += 1
            plain, meta = _inline(" ".join(item))
            if plain:
                out.append(ListItem(plain, md(category_depth=indent // 2 if indent < 4 else indent // 4 + 1, **meta)))
            continue

        # indented code block (only when not continuing a paragraph)
        if line.startswith("    ") and not para:
            body = []
            while i < len(lines) and (lines[i].startswith("    ") or not lines[i].strip()):
                body.append(lines[i][4:]); i += 1
            code = "\n".join(body).strip("\n")
            if code:
                out.append(CodeSnippet(code, md()))
            continue

        para.append(line)
        i += 1
    flush_para()
    return out


def partition_md(
    filename: Optional[str] = None,
    file: Optional[FileLike] = None,
    text: Optional[str] = None,
    encoding: Optional[str] = None,
    metadata_filename: Optional[str] = None,
    languages: Optional[list[str]] = None,
    unique_element_ids: bool = False,
    **_: object,
) -> list[Element]:
    if text is None:
        text = decode_text(read_bytes(filename, file), encoding)
    # strip YAML front matter
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            text = text[end + 4:]
    base = base_metadata(filename, "text/markdown", metadata_filename)
    return finalize(markdown_to_elements(text), base, languages, unique_element_ids=unique_element_ids)
