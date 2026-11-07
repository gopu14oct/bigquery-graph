"""Chunking: turn fine-grained elements into retrieval-sized chunks.

Two strategies:

* ``basic``    - fill each chunk with whole consecutive elements up to the limits.
* ``by_title`` - like ``basic`` but a ``Title`` always starts a new chunk (section
  boundaries are respected), optionally also a page change, and small adjacent
  sections are re-combined so a lone heading doesn't become a tiny chunk.

Limits:

* ``max_characters``  - hard limit; no chunk is ever longer.
* ``new_after_n_chars`` - soft limit; once a chunk reaches it, start a new one even
  if the next element would still fit.
* ``overlap`` - characters copied from the end of one text split into the start of
  the next. Applied only when an oversized element has to be split mid-text, or to
  every chunk with ``overlap_all=True``.

Tables are never mixed with text: a table that fits becomes a ``Table`` chunk, a table
that doesn't is split *between rows* into ``TableChunk``s, each carrying a valid
``text_as_html`` fragment.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable, Optional

from docparse.elements import (
    CompositeElement,
    Element,
    ElementMetadata,
    PageBreak,
    Table,
    TableChunk,
    Title,
)
from docparse.partition.html import parse_html, rows_to_html, rows_to_text, table_rows

SEPARATOR = "\n\n"
CHUNK_KWARGS = {
    "max_characters", "new_after_n_chars", "overlap", "overlap_all", "combine_text_under_n_chars",
    "multipage_sections", "include_orig_elements",
}
# Metadata that describes a single element and makes no sense on a merged chunk.
_ELEMENT_ONLY_FIELDS = ("category_depth", "parent_id", "coordinates", "text_as_html", "style_name",
                        "header_footer_type", "is_continuation", "orig_elements", "chunk_index")
_LIST_FIELDS = ("emphasized_text_contents", "emphasized_text_tags", "link_texts", "link_urls")


class ChunkingOptions:
    def __init__(
        self,
        max_characters: int = 500,
        new_after_n_chars: Optional[int] = None,
        overlap: int = 0,
        overlap_all: bool = False,
        combine_text_under_n_chars: Optional[int] = None,
        multipage_sections: bool = True,
        include_orig_elements: bool = True,
    ):
        if max_characters <= 0:
            raise ValueError("max_characters must be > 0")
        if overlap < 0 or overlap >= max_characters:
            raise ValueError("overlap must be >= 0 and < max_characters")
        self.hard = max_characters
        self.soft = min(new_after_n_chars if new_after_n_chars is not None else max_characters, max_characters)
        if self.soft < 0:
            raise ValueError("new_after_n_chars must be >= 0")
        self.overlap = overlap
        self.overlap_all = overlap_all
        combine = combine_text_under_n_chars if combine_text_under_n_chars is not None else max_characters
        self.combine_under = min(combine, self.soft)
        self.multipage = multipage_sections
        self.include_orig = include_orig_elements
        # with overlap_all, reserve room for the prefix carried over from the previous chunk
        self.text_budget = self.hard - (overlap if overlap_all else 0)


# ------------------------------------------------------------------ text splitting
def split_text(text: str, max_chars: int, overlap: int = 0) -> list[str]:
    """Split ``text`` into windows of at most ``max_chars``, cutting preferably at a
    paragraph break, then a sentence end, then whitespace; never mid-word unless a
    single word is longer than the window."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    out: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        if n - start <= max_chars:
            out.append(text[start:].strip())
            break
        window = text[start:start + max_chars]
        cut = None
        min_cut = max_chars // 2
        for pattern in (r"\n\s*\n", r"\n", r"[.!?][\"')\]]*\s", r"[;:,]\s", r"\s"):
            matches = [m.end() for m in re.finditer(pattern, window) if m.end() >= min_cut]
            if matches:
                cut = matches[-1]
                break
        if cut is None:
            cut = max_chars
        piece = text[start:start + cut].strip()
        if piece:
            out.append(piece)
        if overlap:
            nxt = start + cut - overlap
            # start the overlap on a word boundary when one exists inside the overlap window
            piece_end = start + len(text[start:start + cut].rstrip())
            ws = text.find(" ", nxt, piece_end)
            nxt = ws + 1 if ws != -1 else nxt
            start = max(nxt, start + 1)
        else:
            start += cut
        while start < n and text[start].isspace():
            start += 1
    return [p for p in out if p]


# ------------------------------------------------------------------ pre-chunking
def _sections(elements: list[Element], by_title: bool, multipage: bool) -> list[list[Element]]:
    sections: list[list[Element]] = []
    cur: list[Element] = []
    last_page = None
    for el in elements:
        page = el.metadata.page_number
        boundary = (by_title and isinstance(el, Title)) or (
            not multipage and last_page is not None and page is not None and page != last_page)
        if boundary and cur:
            sections.append(cur)
            cur = []
        cur.append(el)
        if page is not None:
            last_page = page
    if cur:
        sections.append(cur)
    return sections


def _text_len(els: list[Element]) -> int:
    return len(SEPARATOR.join(e.text for e in els))


def _pre_chunks(section: list[Element], opts: ChunkingOptions) -> list[list[Element]]:
    out: list[list[Element]] = []
    cur: list[Element] = []
    for el in section:
        if isinstance(el, Table):
            if cur:
                out.append(cur); cur = []
            out.append([el])
            continue
        if cur:
            cur_len = _text_len(cur)
            if cur_len >= opts.soft or cur_len + len(SEPARATOR) + len(el.text) > opts.text_budget:
                out.append(cur); cur = []
        cur.append(el)
    if cur:
        out.append(cur)
    return out


def _combine_small(groups: list[list[list[Element]]], opts: ChunkingOptions) -> list[list[Element]]:
    """Merge a section's last pre-chunk with the next section's first while the
    running chunk is under ``combine_text_under_n_chars`` and the result fits."""
    flat: list[list[Element]] = []
    boundary_after: set[int] = set()  # indices i where a section ends after flat[i]
    for g in groups:
        flat.extend(g)
        boundary_after.add(len(flat) - 1)
    out: list[list[Element]] = []
    for i, pc in enumerate(flat):
        if out and (i - 1) in boundary_after:
            prev = out[-1]
            is_table = isinstance(prev[0], Table) or isinstance(pc[0], Table)
            same_page = prev[-1].metadata.page_number == pc[0].metadata.page_number
            merged_len = _text_len(prev + pc)
            if (not is_table and _text_len(prev) < opts.combine_under and merged_len <= opts.text_budget
                    and (opts.multipage or same_page)):
                out[-1] = prev + pc
                continue
        out.append(list(pc))
    return out


# ------------------------------------------------------------------ chunk forming
def _consolidate_metadata(els: list[Element]) -> ElementMetadata:
    md = els[0].metadata.copy()
    for f in _ELEMENT_ONLY_FIELDS:
        setattr(md, f, None)
    langs: list[str] = []
    for e in els:
        for lang in e.metadata.languages or []:
            if lang not in langs:
                langs.append(lang)
    md.languages = langs or None
    for f in _LIST_FIELDS:
        vals: list = []
        for e in els:
            vals.extend(getattr(e.metadata, f) or [])
        setattr(md, f, vals or None)
    return md


def _orig(els: list[Element]) -> list[Element]:
    out = []
    for e in els:
        c = type(e)(e.text, e.metadata.copy(), element_id=e.id)
        c.metadata.orig_elements = None
        out.append(c)
    return out


def _table_chunks(table: Table, opts: ChunkingOptions) -> list[Element]:
    md = table.metadata.copy()
    md.parent_id = md.category_depth = None
    if len(table.text) <= opts.hard:
        return [Table(table.text, md)]
    rows: list[list[str]] = []
    if md.text_as_html:
        tnode = next((n for n in parse_html(md.text_as_html).iter() if n.tag == "table"), None)
        rows = table_rows(tnode) if tnode is not None else []
    if not rows:
        rows = [[ln] for ln in table.text.split("\n")]
    out: list[Element] = []
    batch: list[list[str]] = []

    def emit(rows_):
        m = md.copy()
        m.text_as_html = rows_to_html(rows_)
        m.is_continuation = True if out else None
        out.append(TableChunk(rows_to_text(rows_), m))

    for row in rows:
        row_text = rows_to_text([row])
        if len(row_text) > opts.hard:  # a single monster row: split its text
            if batch:
                emit(batch); batch = []
            for piece in split_text(row_text, opts.hard, opts.overlap):
                m = md.copy(); m.text_as_html = None; m.is_continuation = True if out else None
                out.append(TableChunk(piece, m))
            continue
        if batch and len(rows_to_text(batch + [row])) > opts.hard:
            emit(batch); batch = []
        batch.append(row)
    if batch:
        emit(batch)
    return out


def _text_chunks(els: list[Element], opts: ChunkingOptions) -> list[Element]:
    md = _consolidate_metadata(els)
    text = SEPARATOR.join(e.text for e in els)
    if len(text) <= opts.text_budget:
        chunk = CompositeElement(text, md)
        if opts.include_orig:
            chunk.metadata.orig_elements = _orig(els)
        return [chunk]
    out: list[Element] = []
    for i, piece in enumerate(split_text(text, opts.text_budget, opts.overlap)):
        m = md.copy()
        m.is_continuation = True if i else None
        if opts.include_orig:
            m.orig_elements = _orig(els)
        out.append(CompositeElement(piece, m))
    return out


def _finalize_chunks(chunks: list[Element], opts: ChunkingOptions) -> list[Element]:
    prev_text = ""
    for i, c in enumerate(chunks):
        if opts.overlap_all and opts.overlap and prev_text and isinstance(c, CompositeElement) and not c.metadata.is_continuation:
            tail = prev_text[-opts.overlap:]
            sp = tail.find(" ")
            tail = tail[sp + 1:] if 0 <= sp < len(tail) - 1 else tail
            c.text = f"{tail} {c.text}" if tail else c.text
        prev_text = c.text
        c.metadata.chunk_index = i
        key = f"{c.metadata.filename}|{i}|{c.text}"
        c.id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return chunks


def _run(elements: Iterable[Element], by_title: bool, opts: ChunkingOptions) -> list[Element]:
    els = [e for e in elements if not isinstance(e, PageBreak) and e.text.strip()]
    sections = _sections(els, by_title, opts.multipage)
    groups = [_pre_chunks(s, opts) for s in sections]
    pre = _combine_small(groups, opts) if by_title else [pc for g in groups for pc in g]
    chunks: list[Element] = []
    for pc in pre:
        if len(pc) == 1 and isinstance(pc[0], Table):
            chunks.extend(_table_chunks(pc[0], opts))
        else:
            chunks.extend(_text_chunks(pc, opts))
    return _finalize_chunks(chunks, opts)


def chunk_elements(elements: Iterable[Element], **kwargs) -> list[Element]:
    """The ``basic`` strategy."""
    kwargs.pop("combine_text_under_n_chars", None)
    kwargs.pop("multipage_sections", None)
    return _run(elements, by_title=False, opts=ChunkingOptions(**kwargs))


def chunk_by_title(elements: Iterable[Element], **kwargs) -> list[Element]:
    """The ``by_title`` strategy."""
    return _run(elements, by_title=True, opts=ChunkingOptions(**kwargs))


def chunk(elements: Iterable[Element], strategy: str = "by_title", **kwargs) -> list[Element]:
    if strategy == "basic":
        return chunk_elements(elements, **kwargs)
    if strategy == "by_title":
        return chunk_by_title(elements, **kwargs)
    raise ValueError(f"Unknown chunking strategy {strategy!r}; use 'basic' or 'by_title'.")
