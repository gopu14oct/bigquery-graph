"""Heuristics that decide what kind of element a bare string of text is.

Used whenever the source format carries no semantic markup (plain text, PDF,
un-styled DOCX paragraphs...). No ML model, no NLTK: just fast, explainable rules.
"""

from __future__ import annotations

import re

# Bullet glyphs commonly produced by word processors and PDF exporters.
UNICODE_BULLETS = [
    "\u0095", "•", "‣", "⁃", "⁌", "⁍", "∙", "○",
    "●", "◘", "◦", "☙", "❥", "❧", "⦾", "⦿",
    "▪", "▫", "■", "□", "➢", "✓", "✔", "",
    "", "", "·", "*", "-", "–", "—", "+",
]
BULLETS_RE = re.compile(r"^\s*(?:" + "|".join(re.escape(b) for b in UNICODE_BULLETS) + r")\s+")
# "1." "1)" "(a)" "iv." "A." etc.
ORDERED_BULLET_RE = re.compile(
    r"^\s*(?:\(?\d{1,3}(?:\.\d{1,3})*[.)]|\(?[a-zA-Z][.)]|\(?(?:i{1,3}|iv|v|vi{1,3}|ix|x)[.)])\s+"
)

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")
EMAIL_IN_TEXT_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
US_PHONE_RE = re.compile(r"^\s*(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\s*$")
URL_RE = re.compile(r"https?://\S+")
# "123 Main St, Springfield, IL 62704" style addresses.
_STREET_SUFFIXES = (
    "street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|place|pl|"
    "square|sq|way|parkway|pkwy|highway|hwy|terrace|ter|circle|cir|suite|ste"
)
ADDRESS_RE = re.compile(
    rf"^\d{{1,6}}\s+(?:[A-Za-z0-9.'-]+\s+){{0,5}}(?:{_STREET_SUFFIXES})\b\.?"
    r"(?:[ ,]+[A-Za-z0-9 .'-]+){0,4}?(?:[ ,]+[A-Z]{2}\s+\d{5}(?:-\d{4})?)?\s*$",
    re.IGNORECASE,
)
ENDS_IN_PUNCT_RE = re.compile(r"[.!?,;:]\s*$")
SENTENCE_END_RE = re.compile(r"[.!?](?:[\"')\]]+)?(?:\s|$)")
PAGE_NUMBER_RE = re.compile(r"^\s*(?:page\s+)?(?:\d{1,4}(?:\s*(?:/|of)\s*\d{1,4})?|[ivxlc]{1,6})\s*$", re.IGNORECASE)

# Small closed-class word list used as a cheap "does this look like a sentence" signal.
_FUNCTION_WORDS = frozenset(
    """a an the is are was were be been being has have had do does did will would can could
    should may might must shall of in on at to for with by from as that this these those it its
    and or but if then than which who whom whose what when where why how not no we you they he
    she i our your their his her""".split()
)
_COMMON_VERB_SUFFIX_RE = re.compile(r"\b\w+(?:ed|ing|es|ize|ise|ates?)\b", re.IGNORECASE)


def _alpha_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(c.isalpha() for c in chars) / len(chars)


def _cap_ratio(text: str) -> float:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", text)]
    if not words:
        return 0.0
    return sum(w[0].isupper() for w in words) / len(words)


def word_count(text: str) -> int:
    return len(text.split())


def sentence_count(text: str, min_words: int = 3) -> int:
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]
    return sum(1 for p in parts if word_count(p) >= min_words)


def looks_like_sentence(text: str) -> bool:
    words = [w.lower().strip(".,;:!?()\"'") for w in text.split()]
    if any(w in _FUNCTION_WORDS for w in words):
        return True
    return bool(_COMMON_VERB_SUFFIX_RE.search(text))


def is_bulleted_text(text: str) -> bool:
    return bool(BULLETS_RE.match(text) or ORDERED_BULLET_RE.match(text))


def is_email_address(text: str) -> bool:
    return bool(EMAIL_RE.match(text.strip()))


def is_us_phone_number(text: str) -> bool:
    return bool(US_PHONE_RE.match(text))


def is_possible_address(text: str) -> bool:
    t = " ".join(text.split())
    return len(t) < 120 and bool(ADDRESS_RE.match(t))


def is_page_number(text: str) -> bool:
    return bool(PAGE_NUMBER_RE.match(text))


def is_possible_title(text: str, max_words: int = 12, non_alpha_threshold: float = 0.5) -> bool:
    t = text.strip()
    if not t or len(t) > 200:
        return False
    if word_count(t) > max_words:
        return False
    if t.isnumeric() or is_page_number(t):
        return False
    if _alpha_ratio(t) < non_alpha_threshold:
        return False
    if t.endswith((",", ";", ":")):  # salutations and field labels ("Authors:")
        return False
    # Full sentences ending in a period are usually body text, unless all caps.
    if t.endswith(".") and not t.isupper() and word_count(t) > 3:
        return False
    if t.isupper() and t.endswith((".", "!", "?")):
        return False
    if sentence_count(t, 3) > 1:
        return False
    if URL_RE.search(t) or EMAIL_IN_TEXT_RE.search(t):
        return False
    # "Label: value" pairs are field data, not headings ("Section 2: Results" is fine).
    head, sep, tail = t.partition(": ")
    if sep and tail and not re.match(r"^(?:chapter|section|part|appendix|step|lesson|module)\b", head, re.I):
        return False
    return True


def is_possible_narrative_text(text: str, cap_threshold: float = 0.5, non_alpha_threshold: float = 0.5) -> bool:
    t = text.strip()
    if not t or t.isnumeric():
        return False
    if _alpha_ratio(t) < non_alpha_threshold:
        return False
    if word_count(t) < 3:
        return False
    # Text where most words are Capitalised is a heading-ish fragment, not prose.
    if _cap_ratio(t) > cap_threshold and word_count(t) < 25:
        return False
    if sentence_count(t, 3) < 2 and not looks_like_sentence(t):
        return False
    return True
