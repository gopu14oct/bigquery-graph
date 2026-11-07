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
    # a text value ends at the end of its first sentence when more text follows ("30 days. Questions? ...")
    m = re.match(r"(.{2,}?[.!?])\s+[A-Z(\"']", v)
    if m and len(m.group(1)) >= 3:
        v = m.group(1).rstrip(".")
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
                    out.append({"label": cells[0], "value": cells[-1], "el": el, "how": "table row", "ncells": len(cells)})
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


# ---------------------------------------------------------------- field suggestions
DOC_TYPES = {
    "invoice": ["invoice", "bill to", "due date", "subtotal", "amount due", "invoice number", "vat", "remit", "payment terms"],
    "receipt": ["receipt", "thank you for your purchase", "change due", "cash", "card ending", "cashier", "subtotal", "transaction id", "total paid"],
    "contract": ["agreement", "party", "parties", "hereby", "term", "termination", "governing law", "whereas", "shall", "indemn"],
    "resume": ["experience", "education", "skills", "curriculum vitae", "resume", "employment", "linkedin", "references"],
    "paper": ["abstract", "introduction", "related work", "references", "et al", "conclusion", "method", "results", "arxiv"],
}


_STOP = {"in", "on", "at", "to", "of", "and", "or", "the", "a", "an", "for", "by", "with", "from", "is", "as", "see", "note", "e.g", "i.e"}
_NOT_LABEL_RE = re.compile(r"^(?:https?|ftp|www|mailto|doi|urn)$|^\d|^(?:table|figure|fig|tab|eq|equation|section|chapter|step)\.?\s*\d", re.I)


def _clean_label(label: str) -> Optional[str]:
    lab = " ".join(label.replace("*", "").split()).strip(" -–—#.:;|")
    if not (3 <= len(lab) <= 40) or len(lab.split()) > 5 or lab.lower() in _STOP or _NOT_LABEL_RE.search(lab):
        return None
    if sum(c.isalpha() for c in lab) < 0.6 * len(lab.replace(" ", "")):
        return None
    if lab.endswith((".", "?", "!")) or lab[0].islower() and len(lab.split()) > 3:
        return None
    return lab[0].upper() + lab[1:]


def _infer_type(label: str, value: str) -> str:
    lab = label.lower()
    head = value.strip()[:40]  # judge the type from the start of the value, not something said later
    for t in ("email", "date", "money"):
        v = _fit(head, t)
        if v and head.find(v) <= 3 and (t != "money" or MONEY_RE.search(head)):
            return t
    if _fit(value, "phone") and re.search(r"phone|tel|mobile|fax|call", lab):
        return "phone"
    if re.search(r"\b(no|number|#|id|code|ref|reference|account|po)\b", lab) and _fit(value, "id"):
        return "id"
    if NUMBER_RE.fullmatch(value.strip().replace(" ", "")):
        return "number"
    return "text"


def suggest(elements: list[dict], limit: int = 14) -> dict:
    """Propose fields for this document from its own structure, plus a document-type guess."""
    body = [e for e in elements if e["type"] not in FURNITURE]
    text = " ".join(e["text"] for e in body[:400]).lower()
    # count distinct keywords present (whole words), not raw frequency: one common word
    # repeated many times must not decide the type
    scores = {k: sum(1 for w in ws if re.search(rf"\b{re.escape(w)}\b", text)) for k, ws in DOC_TYPES.items()}
    doc_type, best = max(scores.items(), key=lambda kv: kv[1]) if scores else (None, 0)
    runner_up = sorted(scores.values())[-2] if len(scores) > 1 else 0
    total = sum(scores.values()) or 1
    if best < 3 or best - runner_up < 2:
        doc_type, best = None, 0
    out: dict[str, dict] = {}
    for c in candidates(body):
        lab = _clean_label(c["label"])
        # table columns and full data rows (more than label + value) are not single fields
        if not lab or c["how"] == "table column" or c.get("ncells", 2) > 2:
            continue
        val = " ".join(c["value"].split())
        if not val or len(val) > 160 or val.startswith("//") or not re.search(r"[A-Za-z0-9]", val):
            continue
        key = _norm(lab)
        if key in out:
            out[key]["score"] += 1
            continue
        ftype = _infer_type(lab, val)
        # Short values are what fields usually hold; sentence-like values (definition lists, prose
        # after a colon) are kept but ranked lower.
        wordy = ftype == "text" and len(val.split()) > 8
        out[key] = {"name": lab, "type": ftype, "aliases": [], "example": _fit(val, ftype) or val[:60],
                    "element_id": c["el"]["element_id"], "score": 2 + (ftype != "text") + (c["how"] == "label: value") - 1.5 * wordy,
                    "why": "labelled in the document"}
    for i, el in enumerate(e for e in body if e["type"] in ("Table", "TableChunk")):
        grid = [r for r in _grid(el) if any(r)]
        if len(grid) >= 3 and max(len(r) for r in grid) >= 3:
            heads = [h for h in grid[0] if h]
            name = f"Table {i + 1}" if not heads else (heads[0] if len(heads) == 1 else f"{heads[0]} table")
            key = _norm(name) + "#table"
            out.setdefault(key, {"name": name, "type": "list", "aliases": heads[:3], "example": " | ".join(heads[:4]),
                                 "element_id": el["element_id"], "score": 2 + min(len(grid), 10) / 5, "why": f"table with {len(grid) - 1} rows"})
    have = {f["type"] for f in out.values()}
    for ftype, name in (("email", "Email"), ("phone", "Phone")):
        if ftype in have:
            continue
        for el in body:
            v = _fit(el["text"], ftype)
            # an unlabelled number only counts as a phone if it is written like one
            if v and ftype == "phone" and not re.search(r"^\+|[()\s-]", v.strip()):
                v = None
            if v:
                out[_norm(name)] = {"name": name, "type": ftype, "aliases": [], "example": v, "element_id": el["element_id"],
                                    "score": 1.5, "why": f"{name.lower()} found in the text"}
                break
    fields = sorted(out.values(), key=lambda f: -f["score"])[:limit]
    return {"doc_type": doc_type, "doc_type_confidence": round(best / total, 2) if doc_type else 0.0,
            "fields": fields}
