"""Field extraction from parsed elements, without AI ("Quick extract").

Given the elements docparse produced and a list of fields to find, return a value for each
field plus the element it came from. Works on the structure parsing recovered:

1. Label/value candidates
   * text elements shaped "Label: value" (also several pairs on one line split by wide gaps)
   * two-column tables (label | value rows)
   * other tables: a row whose first cell is a label (e.g. "Total | | $110.00") gives its
     last non-empty cell; a header cell gives the first value under it
2. For each field, the candidate whose label best matches the field's name or aliases and
   whose value fits the field's type (money, date, number, id, email, phone, text) wins.
3. Fallbacks: an element that *starts with* an alias followed by a value; for email/phone/
   date/money, the first value of that type anywhere (low confidence).
4. Fields of type "list" take the largest multi-column table as a list of row objects.

Every result says how it was found (`method`) and how sure it is (`confidence` 0-1), so the
UI can show weak answers as such. Used by the server and by the in-browser build.
"""

from __future__ import annotations

import html as _html
import re
from difflib import SequenceMatcher
from typing import Any, Optional

FURNITURE = {"Header", "Footer", "PageBreak"}

MONEY_RE = re.compile(r"(?:[$€£¥₹]|USD|EUR|GBP|INR)\s?-?\d[\d,]*(?:\.\d{1,2})?|-?\d[\d,]*\.\d{2}\s?(?:USD|EUR|GBP|INR|[$€£])?")
NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE_RE = re.compile(
    rf"\b(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}|{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\.?,?\s+\d{{4}})\b", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}")
ID_RE = re.compile(r"#?\s?([A-Z0-9][A-Z0-9-/]{2,})", re.I)
PAIR_RE = re.compile(r"^\s*([^:：]{1,60}?)\s*[:：]\s*(.+?)\s*$")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def _label_score(label: str, aliases: list[str]) -> float:
    lab = _norm(label)
    if not lab:
        return 0.0
    best = 0.0
    for a in aliases:
        al = _norm(a)
        if not al:
            continue
        if lab == al:
            return 1.0
        if re.search(rf"\b{re.escape(al)}\b", lab) and len(lab) <= len(al) + 18:
            best = max(best, 0.9)
        best = max(best, SequenceMatcher(None, lab, al).ratio() * 0.95)
    return best


def _fit(value: str, ftype: str) -> Optional[str]:
    """The part of `value` that fits the field type, or None."""
    v = " ".join(value.split())
    if not v:
        return None
    if ftype == "money":
        m = MONEY_RE.search(v) or NUMBER_RE.search(v)
        return m.group(0).strip() if m else None
    if ftype == "number":
        m = NUMBER_RE.search(v)
        return m.group(0) if m else None
    if ftype == "date":
        m = DATE_RE.search(v)
        return m.group(0) if m else None
    if ftype == "email":
        m = EMAIL_RE.search(v)
        return m.group(0) if m else None
    if ftype == "phone":
        m = PHONE_RE.search(v)
        return m.group(0).strip() if m and sum(c.isdigit() for c in m.group(0)) >= 7 else None
    if ftype == "id":
        m = ID_RE.search(v)
        return m.group(1) if m and any(c.isdigit() for c in m.group(1)) else None
    return v if len(v) <= 300 else v[:300] + "…"


def _grid(el: dict) -> list[list[str]]:
    h = el["metadata"].get("text_as_html") or ""
    rows = re.findall(r"<tr>(.*?)</tr>", h, re.S)
    return [[_html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)] for r in rows]


def candidates(elements: list[dict]) -> list[dict]:
    """All label/value pairs the document's structure offers."""
    out: list[dict] = []
    for el in elements:
        if el["type"] in FURNITURE:
            continue
        if el["type"] in ("Table", "TableChunk"):
            grid = [r for r in _grid(el) if any(r)]
            if not grid:
                continue
            width = max(len(r) for r in grid)
            for r in grid:
                cells = [c for c in r if c]
                if len(cells) >= 2 and not NUMBER_RE.fullmatch(cells[0].replace(" ", "")):
                    # label in the first cell, value in the last non-empty cell (totals rows, 2-col tables)
                    out.append({"label": cells[0], "value": cells[-1], "el": el, "how": "table row"})
            if width >= 2 and len(grid) >= 2:
                for c, head in enumerate(grid[0]):
                    if head:
                        first = next((r[c] for r in grid[1:] if c < len(r) and r[c]), "")
                        if first:
                            out.append({"label": head, "value": first, "el": el, "how": "table column"})
            continue
        for part in re.split(r"\s{3,}|\s\|\s|\n", el["text"]):
            m = PAIR_RE.match(part)
            if m and not m.group(1).strip().isdigit():
                out.append({"label": m.group(1), "value": m.group(2), "el": el, "how": "label: value"})
    return out


def _snippet(el: dict, value: str) -> str:
    t = " ".join(el["text"].split())
    i = t.lower().find(value.lower()[:40])
    if i < 0:
        return t[:160]
    a = max(0, i - 60)
    return ("…" if a else "") + t[a:i + len(value) + 60] + ("…" if i + len(value) + 60 < len(t) else "")


def _result(field: dict, value: Any, el: Optional[dict], confidence: float, method: str) -> dict:
    return {"name": field["name"], "value": value, "element_id": el["element_id"] if el else None,
            "page": el["metadata"].get("page_number") if el else None,
            "snippet": _snippet(el, value if isinstance(value, str) else "") if el and isinstance(value, str) else (el["text"][:160] if el else None),
            "confidence": round(confidence, 2), "method": method}


def extract(elements: list[dict], fields: list[dict]) -> list[dict]:
    """fields: [{name, type?: text|money|date|number|id|email|phone|list, aliases?: [..]}]"""
    body = [e for e in elements if e["type"] not in FURNITURE]
    cands = candidates(body)
    results = []
    for f in fields:
        ftype = f.get("type", "text")
        aliases = [f["name"], *f.get("aliases", [])]
        if ftype == "list":
            tables = [e for e in body if e["type"] in ("Table", "TableChunk")]
            best = max(tables, key=lambda e: len(_grid(e)) * max((len(r) for r in _grid(e)), default=0), default=None)
            if best is not None:
                grid = [r for r in _grid(best) if any(r)]
                if len(grid) >= 2 and max(len(r) for r in grid) >= 2:
                    head = [h or f"col{i + 1}" for i, h in enumerate(grid[0])]
                    rows = [dict(zip(head, r)) for r in grid[1:]]
                    results.append(_result(f, rows, best, 0.6, "largest table"))
                    continue
            results.append(_result(f, None, None, 0.0, "not found"))
            continue
        # 1. label/value candidates
        best = None
        for c in cands:
            s = _label_score(c["label"], aliases)
            if s < 0.72:
                continue
            v = _fit(c["value"], ftype)
            if v is None:
                continue
            conf = 0.55 + 0.4 * s
            if best is None or conf > best[0]:
                best = (conf, v, c["el"], c["how"])
        if best:
            results.append(_result(f, best[1], best[2], best[0], best[3]))
            continue
        # 2. element starting with an alias ("Invoice #4521", "Total $110.00")
        found = None
        for el in body:
            t = " ".join(el["text"].split())
            for a in aliases:
                m = re.match(rf"\s*{re.escape(a)}\b\s*[#:.\-–]?\s*(.+)$", t, re.I)
                if m:
                    v = _fit(m.group(1), ftype)
                    if v:
                        found = (v, el)
                        break
            if found:
                break
        if found:
            results.append(_result(f, found[0], found[1], 0.6, "text after label"))
            continue
        # 3. alias anywhere, value of the right type right after it
        if ftype in ("money", "date", "id", "number", "email", "phone"):
            for el in body:
                t = " ".join(el["text"].split())
                for a in aliases:
                    i = t.lower().find(a.lower())
                    if i >= 0:
                        v = _fit(t[i + len(a): i + len(a) + 80], ftype)
                        if v:
                            found = (v, el)
                            break
                if found:
                    break
            if found:
                results.append(_result(f, found[0], found[1], 0.45, "near label"))
                continue
        # 4. type-only guess for very distinctive types
        if ftype in ("email", "phone"):
            for el in body:
                v = _fit(el["text"], ftype)
                if v:
                    results.append(_result(f, v, el, 0.3, "first of its type"))
                    break
            else:
                results.append(_result(f, None, None, 0.0, "not found"))
            continue
        results.append(_result(f, None, None, 0.0, "not found"))
    return results
