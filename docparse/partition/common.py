"""Shared steps every partitioner runs: text classification and post-processing."""

from __future__ import annotations

import datetime as _dt
import os
from typing import IO, Optional, Union

from docparse import text_type as tt
from docparse.cleaners import clean_bullets, clean_extra_whitespace
from docparse.elements import (
    Address,
    Element,
    ElementMetadata,
    EmailAddress,
    Image,
    ListItem,
    NarrativeText,
    PageBreak,
    Text,
    Title,
    UncategorizedText,
)

FileLike = Union[IO[bytes], IO[str]]


def classify_text(text: str, metadata: Optional[ElementMetadata] = None) -> Optional[Element]:
    """Turn a raw string into the most plausible element type, or ``None`` if empty."""
    md = metadata if metadata is not None else ElementMetadata()
    raw = text.strip()
    if not raw:
        return None
    if tt.is_bulleted_text(raw):
        # Bullet glyphs carry no information and are removed; list *numbers* ("124.",
        # "(b)") are kept because other text refers to them (citations, cross-refs).
        cleaned = clean_bullets(raw) if tt.BULLETS_RE.match(raw) else raw
        return ListItem(text=clean_extra_whitespace(cleaned), metadata=md)
    text = clean_extra_whitespace(raw)
    if tt.is_email_address(text):
        return EmailAddress(text=text, metadata=md)
    if tt.is_us_phone_number(text):
        return Text(text=text, metadata=md)
    if tt.is_possible_address(text):
        return Address(text=text, metadata=md)
    if tt.is_possible_narrative_text(text):
        return NarrativeText(text=text, metadata=md)
    if tt.is_possible_title(text):
        return Title(text=text, metadata=md)
    return UncategorizedText(text=text, metadata=md)


# ---------------------------------------------------------------- file helpers
def read_bytes(filename: Optional[str], file: Optional[FileLike]) -> bytes:
    if filename is not None:
        with open(filename, "rb") as f:
            return f.read()
    if file is None:
        raise ValueError("Either `filename` or `file` must be provided.")
    if hasattr(file, "seek"):
        try:
            file.seek(0)
        except Exception:  # pragma: no cover - non-seekable streams
            pass
    data = file.read()
    return data.encode("utf-8") if isinstance(data, str) else data


def decode_text(data: bytes, encoding: Optional[str] = None) -> str:
    if encoding:
        return data.decode(encoding)
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")  # pragma: no cover


def base_metadata(
    filename: Optional[str],
    filetype: str,
    metadata_filename: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> ElementMetadata:
    name = metadata_filename or filename
    md = ElementMetadata(filetype=filetype)
    if name:
        md.filename = os.path.basename(name)
        directory = os.path.dirname(name)
        md.file_directory = directory or None
    if last_modified is None and filename and os.path.exists(filename):
        ts = os.path.getmtime(filename)
        # same format as unstructured: local time, second precision, no offset
        last_modified = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
    md.last_modified = last_modified
    return md


# ---------------------------------------------------------------- post-processing
def assign_hierarchy(elements: list[Element]) -> None:
    """Fill ``parent_id`` (and default ``category_depth``) from document order.

    * A ``Title`` with depth *d* is parented to the nearest preceding title with depth < d.
    * A ``ListItem`` with depth *d > 0* is parented to the nearest preceding list item of depth d-1.
    * Every other element is parented to the most recent title.
    A ``PageBreak`` does not reset the hierarchy (sections continue across pages).
    """
    title_stack: list[tuple[int, Element]] = []
    list_stack: list[Element] = []
    for el in elements:
        if isinstance(el, PageBreak):
            continue
        md = el.metadata
        if isinstance(el, Title):
            if md.category_depth is None:
                # Heuristic title (no explicit heading level): nest it one level below the
                # nearest *explicit* heading so it does not outrank real document headings.
                explicit = [d for d, t in title_stack if not getattr(t, "_inferred_depth", False)]
                md.category_depth = explicit[-1] + 1 if explicit else 0
                el._inferred_depth = True  # type: ignore[attr-defined]
            depth = md.category_depth
            while title_stack and title_stack[-1][0] >= depth:
                title_stack.pop()
            if md.parent_id is None and title_stack:
                md.parent_id = title_stack[-1][1].id
            title_stack.append((depth, el))
            list_stack = []
            continue
        if isinstance(el, ListItem):
            depth = md.category_depth if md.category_depth is not None else 0
            md.category_depth = depth
            del list_stack[depth:]
            if md.parent_id is None:
                if depth > 0 and len(list_stack) >= depth:
                    md.parent_id = list_stack[depth - 1].id
                elif title_stack:
                    md.parent_id = title_stack[-1][1].id
            list_stack.append(el)
            continue
        list_stack = []
        if md.parent_id is None and title_stack and md.header_footer_type is None:
            md.parent_id = title_stack[-1][1].id


ISO639_1_TO_3 = {
    "en": "eng", "fr": "fra", "de": "deu", "es": "spa", "it": "ita", "pt": "por", "nl": "nld", "ru": "rus",
    "zh": "zho", "ja": "jpn", "ko": "kor", "ar": "ara", "hi": "hin", "sv": "swe", "da": "dan", "no": "nor",
    "fi": "fin", "pl": "pol", "tr": "tur", "cs": "ces", "el": "ell", "he": "heb", "uk": "ukr", "vi": "vie",
}


def normalize_languages(languages: Optional[list[str]]) -> list[str]:
    """ISO 639-3 codes like unstructured (``"en"``/``"en-US"`` -> ``"eng"``); default ``["eng"]``."""
    if not languages:
        return ["eng"]
    out = []
    for lang in languages:
        code = lang.split("-")[0].split("_")[0].lower()
        code = ISO639_1_TO_3.get(code, code)
        if code not in out:
            out.append(code)
    return out


def finalize(
    elements: list[Element],
    base: ElementMetadata,
    languages: Optional[list[str]] = None,
    include_page_breaks: bool = False,
    unique_element_ids: bool = False,
) -> list[Element]:
    """Merge base metadata, drop empties, assign stable ids and hierarchy."""
    out: list[Element] = []
    for el in elements:
        if not isinstance(el, (PageBreak, Image)) and not el.text.strip():
            continue
        if isinstance(el, PageBreak) and not include_page_breaks:
            continue
        md = el.metadata
        for field in ("filename", "file_directory", "filetype", "last_modified", "url"):
            if getattr(md, field) is None:
                setattr(md, field, getattr(base, field))
        if md.languages is None:
            md.languages = normalize_languages(languages)
        out.append(el)

    # Ids must exist before parent links are computed; the hash depends on position
    # *within the page* so editing page 10 does not change the ids on page 1.
    per_page: dict[Optional[int], int] = {}
    for el in out:
        if unique_element_ids:
            el.id  # noqa: B018 - force a uuid
            continue
        seq = per_page.get(el.metadata.page_number, 0)
        per_page[el.metadata.page_number] = seq + 1
        el.assign_hash_id(seq)
    assign_hierarchy(out)
    return out
