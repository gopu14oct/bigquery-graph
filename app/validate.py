"""Automatic parse-quality checks ("parse health") shown in the app's Validate tab.

Each check returns pass / warn / fail / info with a human-readable detail. None of
these needs ground truth: they flag the symptoms real parsing failures produce
(missing text, fragmentation, giant merged blocks, empty pages, header leakage...).
"""

from __future__ import annotations

import collections
import re
import shutil
import subprocess
from typing import Any, Optional

WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
FURNITURE = {"Header", "Footer", "PageBreak", "PageNumber"}


def _words(text: str) -> collections.Counter:
    return collections.Counter(w.lower() for w in WORD_RE.findall(text) if len(w) > 1 or w.isdigit())


def reference_text(path: str) -> Optional[str]:
    """Independent text extraction for PDFs (poppler's pdftotext) - not docparse."""
    if shutil.which("pdftotext") is None:
        return None
    try:
        out = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"], capture_output=True, timeout=120)
        return out.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return None


def check(cid: str, label: str, status: str, detail: str, value: Any = None) -> dict:
    return {"id": cid, "label": label, "status": status, "detail": detail, "value": value}


def validate(elements: list[dict], filetype: str, path: Optional[str], page_count: Optional[int],
             text_layer_pages: Optional[list[int]] = None) -> dict:
    checks: list[dict] = []
    body = [e for e in elements if e["type"] not in FURNITURE]
    types = collections.Counter(e["type"] for e in elements)
    n = max(1, len(body))

    # 1. text coverage against an independent extractor
    if filetype == "application/pdf" and path:
        ref = reference_text(path)
        if ref is not None and sum(_words(ref).values()) >= 30:
            r, h = _words(ref), _words(" ".join(e["text"] for e in elements))
            inter = sum((r & h).values())
            recall = inter / max(1, sum(r.values()))
            precision = inter / max(1, sum(h.values()))
            st = "pass" if recall >= 0.95 else "warn" if recall >= 0.8 else "fail"
            checks.append(check("coverage", "Text coverage vs. pdftotext", st,
                                f"{recall:.1%} of the words an independent extractor finds are present "
                                f"(precision {precision:.1%}).", round(recall, 4)))
        elif ref is not None:
            checks.append(check("coverage", "Text coverage vs. pdftotext", "info",
                                "The PDF has (almost) no text layer: content comes from OCR, so coverage can't be cross-checked.", None))

    # 2. empty pages
    if page_count:
        pages_with = {e["metadata"].get("page_number") for e in body}
        empty = [p for p in range(1, page_count + 1) if p not in pages_with]
        st = "pass" if not empty else "warn" if len(empty) <= max(1, page_count // 10) else "fail"
        checks.append(check("empty_pages", "Pages with content", st,
                            "Every page produced content." if not empty else
                            f"{len(empty)} of {page_count} pages produced no content: {', '.join(map(str, empty[:15]))}"
                            f"{'…' if len(empty) > 15 else ''}.", empty))
    if text_layer_pages is not None and page_count:
        ocr = [p for p in range(1, page_count + 1) if p not in text_layer_pages]
        if ocr:
            checks.append(check("ocr", "OCR pages", "info",
                                f"{len(ocr)} page(s) have no text layer and were read with OCR: {', '.join(map(str, ocr[:15]))}.", ocr))

    # 3. fragmentation
    frag = sum(1 for e in body if len(e["text"].strip()) <= 3)
    ratio = frag / n
    checks.append(check("fragments", "Fragmentation", "pass" if ratio < 0.05 else "warn" if ratio < 0.15 else "fail",
                        f"{frag} of {len(body)} elements are 3 characters or fewer ({ratio:.0%}). High values point to broken lines, "
                        "diagram labels or letter-spaced text.", round(ratio, 4)))

    # 4. over-merged blocks
    big = [e for e in body if e["type"] not in ("Table", "TableChunk") and len(e["text"]) > 4000]
    checks.append(check("merged", "Over-merged blocks", "pass" if not big else "warn",
                        "No suspiciously long text blocks." if not big else
                        f"{len(big)} block(s) longer than 4,000 characters: paragraphs may have been merged.", len(big)))

    # 5. headings & hierarchy
    titles = [e for e in body if e["type"] == "Title"]
    if not titles:
        checks.append(check("headings", "Headings", "warn" if len(body) > 30 else "info",
                            "No headings detected: the outline and section-aware chunking will be flat.", 0))
    else:
        tr = len(titles) / n
        depths = collections.Counter(e["metadata"].get("category_depth", 0) for e in titles)
        st = "pass" if tr <= 0.35 else "warn"
        checks.append(check("headings", "Headings", st,
                            f"{len(titles)} headings across {len(depths)} level(s) "
                            f"({', '.join(f'L{d + 1}: {c}' for d, c in sorted(depths.items()))}). "
                            + ("" if st == "pass" else f"{tr:.0%} of elements are headings, which is unusually high (short lines mistaken for titles?)."),
                            dict(sorted(depths.items()))))
        orphans = sum(1 for e in body if e["type"] != "Title" and not e["metadata"].get("parent_id"))
        checks.append(check("hierarchy", "Section hierarchy",
                            "pass" if orphans / n < 0.1 else "warn",
                            f"{len(body) - orphans} of {len(body)} elements are attached to a section; {orphans} sit before the first heading.",
                            orphans))

    # 6. tables
    tables = [e for e in body if e["type"] in ("Table", "TableChunk")]
    if tables:
        empty_cells = total_cells = 0
        for t in tables:
            cells = re.findall(r"<td>(.*?)</td>", t["metadata"].get("text_as_html") or "")
            total_cells += len(cells)
            empty_cells += sum(1 for c in cells if not c.strip())
        er = empty_cells / max(1, total_cells)
        checks.append(check("tables", "Tables", "pass" if er < 0.4 else "warn",
                            f"{len(tables)} table(s), {total_cells} cells, {er:.0%} empty"
                            + ("." if er < 0.4 else ": many empty cells usually mean a misread column grid."), len(tables)))
    else:
        checks.append(check("tables", "Tables", "info", "No tables detected.", 0))

    # 7. furniture
    hf = types.get("Header", 0) + types.get("Footer", 0)
    if page_count and page_count >= 3:
        checks.append(check("furniture", "Headers & footers", "pass" if hf else "info",
                            f"{hf} running header/footer element(s) were separated from body text." if hf else
                            "No running headers/footers found (fine if the document has none).", hf))

    # 8. duplicates
    texts = collections.Counter(e["text"].strip() for e in body if len(e["text"].strip()) > 20)
    dups = sum(c - 1 for c in texts.values() if c > 1)
    checks.append(check("duplicates", "Duplicated text", "pass" if dups / n < 0.05 else "warn",
                        f"{dups} repeated block(s)." + ("" if dups / n < 0.05 else " Repeated boilerplate may pollute search results."), dups))

    # 9. images
    imgs = types.get("Image", 0)
    if imgs:
        caps = sum(1 for e in body if e["type"] == "FigureCaption")
        checks.append(check("images", "Figures", "info",
                            f"{imgs} image(s) located, {caps} caption(s) detected. Image contents are not described (no vision model).", imgs))

    weight = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
    scored = [c for c in checks if c["status"] in weight]
    score = round(100 * sum(weight[c["status"]] for c in scored) / max(1, len(scored)))
    return {"score": score, "checks": checks, "type_counts": dict(types.most_common())}
