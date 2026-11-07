"""Plain text partitioner."""

from __future__ import annotations

import re
from typing import Optional

from docparse.cleaners import group_broken_paragraphs
from docparse.elements import Element, ElementMetadata, ListItem
from docparse.partition.common import FileLike, base_metadata, classify_text, decode_text, finalize, read_bytes
from docparse.text_type import BULLETS_RE, ORDERED_BULLET_RE

PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def split_paragraphs(text: str, paragraph_grouper: Optional[bool] = None) -> list[str]:
    """Split text into candidate paragraphs.

    Lines inside a blank-line-delimited block are joined (hard-wrapped prose), except
    when each line is itself a bullet - then each bullet is its own paragraph.
    If the text has *no* blank lines at all, each non-empty line is a paragraph.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n")
    if paragraph_grouper is None:
        paragraph_grouper = bool(PARAGRAPH_SPLIT_RE.search(text))
    if not paragraph_grouper:
        return [ln for ln in text.split("\n") if ln.strip()]
    out: list[str] = []
    for block in PARAGRAPH_SPLIT_RE.split(text):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        current: list[str] = []
        for ln in lines:
            if BULLETS_RE.match(ln) or ORDERED_BULLET_RE.match(ln):
                if current:
                    out.append(" ".join(current))
                current = [ln.strip()]
            else:
                current.append(ln.strip())
        if current:
            out.append(" ".join(current))
    return out


def text_to_elements(text: str, metadata: Optional[ElementMetadata] = None) -> list[Element]:
    elements: list[Element] = []
    for para in split_paragraphs(text):
        indent = len(para) - len(para.lstrip(" \t"))
        el = classify_text(para, (metadata or ElementMetadata()).copy())
        if el is None:
            continue
        if isinstance(el, ListItem):
            el.metadata.category_depth = indent // 2 if indent else 0
        elements.append(el)
    return elements


def partition_text(
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
    base = base_metadata(filename, "text/plain", metadata_filename)
    return finalize(text_to_elements(text), base, languages, unique_element_ids=unique_element_ids)


__all__ = ["partition_text", "split_paragraphs", "text_to_elements", "group_broken_paragraphs"]
