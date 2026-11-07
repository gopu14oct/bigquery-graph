"""Text normalisation helpers, usable on raw strings or (via ``apply``) on elements."""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, Iterable

from docparse.text_type import BULLETS_RE, ORDERED_BULLET_RE

_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
_QUOTES = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "«": '"', "»": '"'}
_DASHES_RE = re.compile(r"[‐-―−\-]")


def clean_extra_whitespace(text: str) -> str:
    text = text.replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", text).strip()


def clean_bullets(text: str) -> str:
    return BULLETS_RE.sub("", text, count=1).strip()


def clean_ordered_bullets(text: str) -> str:
    return ORDERED_BULLET_RE.sub("", text, count=1).strip()


def clean_dashes(text: str) -> str:
    return _DASHES_RE.sub(" ", text).strip()


def clean_trailing_punctuation(text: str) -> str:
    return text.strip().rstrip(".,:;")


def replace_unicode_quotes(text: str) -> str:
    for k, v in _QUOTES.items():
        text = text.replace(k, v)
    return text


def clean_ligatures(text: str) -> str:
    for k, v in _LIGATURES.items():
        text = text.replace(k, v)
    return text


def clean_non_ascii_chars(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def dehyphenate(text: str) -> str:
    """Join words split across lines: ``"infor-\\nmation"`` -> ``"information"``."""
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)


def group_broken_paragraphs(text: str) -> str:
    """Re-flow hard-wrapped text: single newlines inside a paragraph become spaces,
    blank lines stay paragraph boundaries. Bulleted lines are kept on their own line."""
    paragraphs = re.split(r"\n\s*\n", text)
    out = []
    for para in paragraphs:
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        if not lines:
            continue
        if all(BULLETS_RE.match(ln) or ORDERED_BULLET_RE.match(ln) for ln in lines):
            out.append("\n".join(lines))
        else:
            out.append(" ".join(lines))
    return "\n\n".join(out)


def clean(
    text: str,
    extra_whitespace: bool = True,
    dashes: bool = False,
    bullets: bool = False,
    trailing_punctuation: bool = False,
    lowercase: bool = False,
) -> str:
    if bullets:
        text = clean_bullets(text)
    if dashes:
        text = clean_dashes(text)
    if trailing_punctuation:
        text = clean_trailing_punctuation(text)
    if extra_whitespace:
        text = clean_extra_whitespace(text)
    if lowercase:
        text = text.lower()
    return text


def apply(elements: Iterable, *cleaners: Callable[[str], str]) -> list:
    """Apply ``cleaners`` in order to every element's text, in place. Returns the list."""
    elements = list(elements)
    for el in elements:
        for fn in cleaners:
            el.text = fn(el.text)
    return elements
