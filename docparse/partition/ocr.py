"""OCR for image-only PDF pages, feeding the same layout pipeline as born-digital text.

* Text: the ``tesseract`` CLI (TSV output) gives words with pixel boxes. Each word's
  font size is estimated from its OCR line's height, so heading detection by size
  works on scans as well.
* Tables: ruled tables are found directly in the page image (no OpenCV): long dark
  horizontal and vertical pixel runs are the ruling lines, their intersections give
  the cell grid, and OCR words are dropped into cells by their centre point.

Requires the ``tesseract`` binary on PATH (``apt-get install tesseract-ocr``).
"""

from __future__ import annotations

import csv
import io
import shutil
import statistics
import subprocess
from collections import defaultdict
from docparse.cleaners import clean_extra_whitespace


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _runs(mask_1d, min_len: int) -> list[tuple[int, int]]:
    """Start/end (exclusive) of runs of True at least ``min_len`` long."""
    import numpy as np

    padded = np.concatenate([[False], mask_1d, [False]])
    diff = np.diff(padded.astype(np.int8))
    starts, ends = np.where(diff == 1)[0], np.where(diff == -1)[0]
    return [(s, e) for s, e in zip(starts, ends) if e - s >= min_len]


def _cluster(values: list[float], tol: float) -> list[float]:
    out: list[list[float]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(c) / len(c) for c in out]


def detect_ruled_tables(gray, min_rows: int = 2, min_cols: int = 2) -> list[tuple[list[float], list[float]]]:
    """Return tables as (row_line_ys, col_line_xs) in pixel coordinates."""
    import numpy as np

    dark = gray < 128
    h, w = dark.shape
    min_len = max(40, w // 12)
    # horizontal segments: (y, x0, x1)
    hsegs = []
    for y in range(h):
        for x0, x1 in _runs(dark[y], min_len):
            hsegs.append((y, x0, x1))
    if not hsegs:
        return []
    # merge thick lines (consecutive y with same extent)
    lines: list[list[float]] = []
    for y, x0, x1 in hsegs:
        if lines and y - lines[-1][0] <= 3 and abs(x0 - lines[-1][1]) < 6 and abs(x1 - lines[-1][2]) < 6:
            continue
        lines.append([y, x0, x1])
    # group lines sharing the same horizontal extent into table candidates
    groups: dict[tuple[int, int], list[float]] = defaultdict(list)
    for y, x0, x1 in lines:
        key = next((k for k in groups if abs(k[0] - x0) < 8 and abs(k[1] - x1) < 8), (int(x0), int(x1)))
        groups[key].append(y)
    tables = []
    for (x0, x1), ys in groups.items():
        ys = _cluster(ys, 4)
        if len(ys) < min_rows + 1:
            continue
        # split a group into separate tables where the vertical gap is huge
        segments, cur = [], [ys[0]]
        for y in ys[1:]:
            if y - cur[-1] > 120:
                segments.append(cur); cur = [y]
            else:
                cur.append(y)
        segments.append(cur)
        for seg in segments:
            if len(seg) < min_rows + 1:
                continue
            top, bottom = int(seg[0]), int(seg[-1])
            region = dark[top:bottom + 1, max(0, x0 - 3):min(w, x1 + 4)]
            col_fill = region.mean(axis=0)
            xs = [x + max(0, x0 - 3) for x in np.where(col_fill > 0.85)[0]]
            xs = _cluster(xs, 4)
            if len(xs) < min_cols + 1:
                continue
            tables.append((seg, xs))
    return tables


_DESCENDERS = set("gjpqy,;()[]{}|/")
_ASCENDERS = set("bdfhklt")
_CAPS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
# Typical ratios of glyph height to point size (sans and serif text faces).
_ASC_RATIO, _CAP_RATIO = 0.74, 0.68
# Common OCR misreads of bullet glyphs.
_BULLET_MISREADS = {"e", "\u00a2", "\u00b0", "\u00ab", "\u00bb", "\u00a9", "@", "*", "\u2022", "\u00b7", "\u00ae", "\u00a7"}


def ocr_words(image, scale: float, lang: str = "eng") -> list[dict]:
    """Run tesseract on a PIL image; return pdfplumber-style word dicts in PDF points.

    Font size per line is estimated from the pixel height of words that have a tall
    letter (capital/ascender) and no descender: that height is ~0.72 of the point
    size for common fonts. Bold is inferred from ink density relative to the page.
    """
    import numpy as np

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    proc = subprocess.run(["tesseract", "stdin", "stdout", "-l", lang, "--psm", "3", "tsv"],
                          input=buf.getvalue(), capture_output=True, check=True)
    rows = list(csv.DictReader(io.StringIO(proc.stdout.decode("utf-8", errors="replace")), delimiter="\t",
                               quoting=csv.QUOTE_NONE))
    rgb = np.asarray(image.convert("RGB")).astype(np.int16)
    gray = rgb.mean(axis=2)
    raw = []
    for r in rows:
        if r["level"] != "5" or not (r.get("text") or "").strip():
            continue
        if float(r.get("conf", -1) or -1) < 0:
            continue
        left, top, wd, ht = (int(r[k]) for k in ("left", "top", "width", "height"))
        box = gray[top:top + ht, left:left + wd]
        dark = box < 140
        stroke, chroma = 0.0, 0.0
        if dark.any():
            d = np.diff(np.pad(dark.astype(np.int8), ((0, 0), (1, 1))), axis=1)
            runs = (np.where(d == -1)[1] - np.where(d == 1)[1])
            stroke = float(np.median(runs)) if runs.size else 0.0
            px = rgb[top:top + ht, left:left + wd][dark]
            chroma = float((px.max(axis=1) - px.min(axis=1)).mean())
        raw.append((r, left, top, wd, ht, stroke, chroma))
    if not raw:
        return []
    page_stroke = float(np.median([x[5] for x in raw]))
    by_line: dict[tuple, list] = defaultdict(list)
    for x in raw:
        r = x[0]
        by_line[(r["block_num"], r["par_num"], r["line_num"])].append(x)
    words = []
    for items in by_line.values():
        est = []
        for (r, _, _, _, ht, _, _) in items:
            t = r["text"]
            if any(c in _DESCENDERS for c in t):
                continue
            if any(c in _ASCENDERS for c in t):
                est.append(ht / _ASC_RATIO)
            elif any(c in _CAPS for c in t):
                est.append(ht / _CAP_RATIO)
        size = (statistics.median(est) if est else statistics.median(x[4] for x in items)) * scale
        size = round(size * 2) / 2
        stroke = statistics.median(x[5] for x in items)
        chroma = statistics.median(x[6] for x in items)
        # Emphasis: visibly heavier strokes, or coloured text (styled headings).
        emphasized = stroke >= page_stroke * 1.35 or chroma > 60
        for (r, left, top, wd, ht, _, _) in items:
            text = r["text"]
            if text in _BULLET_MISREADS and wd * scale < 0.8 * size:
                text = "\u2022"
            words.append({
                "text": text,
                "x0": left * scale, "x1": (left + wd) * scale,
                "top": top * scale, "bottom": (top + ht) * scale,
                "size": size,
                "fontname": "OCR-Bold" if emphasized else "OCR",
            })
    return words


def ocr_page(page, resolution: int = 300, lang: str = "eng") -> tuple[list[dict], list[tuple[tuple, list[list[str]]]]]:
    """OCR one pdfplumber page. Returns (words outside tables, [(bbox, rows)])."""
    import numpy as np

    from PIL import Image

    img = page.to_image(resolution=resolution).original.convert("RGB")
    scale = 72.0 / resolution
    gray = np.asarray(img.convert("L"))
    detected = detect_ruled_tables(gray)
    if detected:
        # Ruling lines confuse Tesseract's page segmentation (cell text is often
        # skipped entirely), so paint them white before running OCR.
        clean = np.asarray(img).copy()
        pad = max(2, resolution // 60)
        for ys, xs in detected:
            x_lo, x_hi = int(xs[0]) - pad, int(xs[-1]) + pad + 1
            y_lo, y_hi = int(ys[0]) - pad, int(ys[-1]) + pad + 1
            for y in ys:
                clean[max(0, int(y) - pad):int(y) + pad + 1, max(0, x_lo):x_hi] = 255
            for x in xs:
                clean[max(0, y_lo):y_hi, max(0, int(x) - pad):int(x) + pad + 1] = 255
        img = Image.fromarray(clean)
    words = ocr_words(img, scale, lang)
    tables = []
    used = set()
    for ys, xs in detected:
        ys_pt = [y * scale for y in ys]
        xs_pt = [x * scale for x in xs]
        grid = [["" for _ in range(len(xs_pt) - 1)] for _ in range(len(ys_pt) - 1)]
        for i, wd in enumerate(words):
            cx, cy = (wd["x0"] + wd["x1"]) / 2, (wd["top"] + wd["bottom"]) / 2
            if not (xs_pt[0] <= cx <= xs_pt[-1] and ys_pt[0] <= cy <= ys_pt[-1]):
                continue
            r = next((k for k in range(len(ys_pt) - 1) if ys_pt[k] <= cy <= ys_pt[k + 1]), None)
            c = next((k for k in range(len(xs_pt) - 1) if xs_pt[k] <= cx <= xs_pt[k + 1]), None)
            if r is None or c is None:
                continue
            grid[r][c] = (grid[r][c] + " " + wd["text"]).strip()
            used.add(i)
        rows = [[clean_extra_whitespace(c) for c in row] for row in grid if any(row)]
        if rows:
            tables.append(((xs_pt[0], ys_pt[0], xs_pt[-1], ys_pt[-1]), rows))
    return [w for i, w in enumerate(words) if i not in used], tables


def page_needs_ocr(page, words: list[dict], min_chars: int = 20) -> bool:
    chars = sum(len(w["text"]) for w in words)
    return chars < min_chars and bool(page.images)


__all__ = ["ocr_page", "page_needs_ocr", "tesseract_available", "detect_ruled_tables", "ocr_words"]
