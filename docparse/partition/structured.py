"""Partitioners for already-structured formats: CSV/TSV, JSON (element round-trip), XML."""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from typing import Optional

from docparse.cleaners import clean_extra_whitespace
from docparse.elements import Element, Table, elements_from_dicts
from docparse.partition.common import FileLike, base_metadata, classify_text, decode_text, finalize, read_bytes
from docparse.partition.html import rows_to_html, rows_to_text


def partition_csv(
    filename: Optional[str] = None,
    file: Optional[FileLike] = None,
    text: Optional[str] = None,
    delimiter: Optional[str] = None,
    encoding: Optional[str] = None,
    metadata_filename: Optional[str] = None,
    languages: Optional[list[str]] = None,
    unique_element_ids: bool = False,
    filetype: str = "text/csv",
    **_: object,
) -> list[Element]:
    """The whole sheet becomes a single ``Table`` element (chunking splits it by rows)."""
    if text is None:
        text = decode_text(read_bytes(filename, file), encoding)
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",\t;|").delimiter
        except csv.Error:
            delimiter = "\t" if filetype == "text/tab-separated-values" else ","
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    rows = [r for r in rows if any(r)]
    base = base_metadata(filename, filetype, metadata_filename)
    md = base.copy()
    md.text_as_html = rows_to_html(rows)
    return finalize([Table(rows_to_text(rows), md)], base, languages, unique_element_ids=unique_element_ids)


def partition_tsv(**kwargs) -> list[Element]:
    kwargs.setdefault("delimiter", "\t")
    return partition_csv(filetype="text/tab-separated-values", **kwargs)


def partition_json(
    filename: Optional[str] = None,
    file: Optional[FileLike] = None,
    text: Optional[str] = None,
    **_: object,
) -> list[Element]:
    """Load elements previously serialised with ``elements_to_json`` (lossless round-trip)."""
    if text is None:
        text = decode_text(read_bytes(filename, file))
    stripped = text.strip()
    if stripped.startswith("["):
        data = json.loads(stripped)
    else:  # NDJSON / JSONL
        data = [json.loads(ln) for ln in stripped.splitlines() if ln.strip()]
    if not all(isinstance(d, dict) and "type" in d and "text" in d for d in data):
        raise ValueError("JSON input must be a list of serialised elements (objects with 'type' and 'text').")
    return elements_from_dicts(data)


def partition_xml(
    filename: Optional[str] = None,
    file: Optional[FileLike] = None,
    text: Optional[str] = None,
    xml_keep_tags: bool = False,
    metadata_filename: Optional[str] = None,
    languages: Optional[list[str]] = None,
    unique_element_ids: bool = False,
    **_: object,
) -> list[Element]:
    """Emit one element per leaf node with text. With ``xml_keep_tags`` the raw markup
    of each leaf is kept; otherwise its text is classified like plain text."""
    if text is None:
        text = decode_text(read_bytes(filename, file))
    root = ET.fromstring(text)
    base = base_metadata(filename, "application/xml", metadata_filename)
    out: list[Element] = []
    for node in root.iter():
        if len(node) == 0 and node.text and node.text.strip():
            if xml_keep_tags:
                el = classify_text(ET.tostring(node, encoding="unicode").strip(), base.copy())
            else:
                el = classify_text(clean_extra_whitespace(node.text), base.copy())
            if el is not None:
                out.append(el)
    return finalize(out, base, languages, unique_element_ids=unique_element_ids)
