"""PDF partitioner with a hand-written layout analysis.

Decoding PDF content streams (fonts, encodings, CMaps) is delegated to
``pdfplumber``/``pdfminer.six``: we only ask them for *positioned words* and
table cell grids. Everything after that is ours:

1. **Lines**   - words are clustered into lines by vertical overlap.
2. **Blocks**  - lines are merged into paragraphs using line-gap, font-size and
                 indentation changes; bullet-led lines start new blocks.
3. **Columns** - blocks are put in reading order with a recursive XY-cut, so
                 two-column papers read left column then right column.
4. **Typing**  - the body font size is the character-weighted mode; blocks set in a
                 larger (or bold, short) font become ``Title``s whose
                 ``category_depth`` is the rank of their size among heading sizes.
5. **Furniture** - text in the top/bottom margin that repeats across pages
                 (digits normalised) becomes ``Header``/``Footer``.
6. **Tables**  - ruled tables found by pdfplumber become ``Table`` elements; words
                 inside a table's box are removed from the text flow.

Scanned (image-only) pages produce no text; OCR is out of scope.
"""

from __future__ import annotations

import io
import statistics
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from docparse.cleaners import clean_extra_whitespace, clean_ligatures, dehyphenate
from docparse.elements import (
    Coordinates,
    Element,
    ElementMetadata,
    FigureCaption,
    Footer,
    Header,
    Image,
    ListItem,
    PageBreak,
    Table,
    Text,
    Title,
    UncategorizedText,
)
from docparse.partition.common import FileLike, base_metadata, classify_text, finalize, read_bytes
from docparse.partition.html import rows_to_html, rows_to_text
from docparse.partition.ocr import ocr_page, page_needs_ocr, tesseract_available
from docparse.text_type import BULLETS_RE, EMAIL_IN_TEXT_RE, URL_RE, ORDERED_BULLET_RE, is_page_number


@dataclass
class Line:
    words: list[dict]
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    bold: bool

    @property
    def text(self) -> str:
        return " ".join(w["text"] for w in self.words)


@dataclass
class TableBlock:
    x0: float
    top: float
    x1: float
    bottom: float
    rows: list


@dataclass
class Block:
    lines: list[Line] = field(default_factory=list)

    @property
    def x0(self): return min(l.x0 for l in self.lines)
    @property
    def x1(self): return max(l.x1 for l in self.lines)
    @property
    def top(self): return min(l.top for l in self.lines)
    @property
    def bottom(self): return max(l.bottom for l in self.lines)

    @property
    def size(self) -> float:
        total = sum(len(l.text) for l in self.lines) or 1
        return sum(l.size * len(l.text) for l in self.lines) / total

    @property
    def bold(self) -> bool:
        return all(l.bold for l in self.lines)

    @property
    def text(self) -> str:
        return clean_extra_whitespace(dehyphenate("\n".join(l.text for l in self.lines)).replace("\n", " "))


# --------------------------------------------------------------- geometry
def _is_bold(fontname: str) -> bool:
    f = fontname.lower()
    return any(k in f for k in ("bold", "black", "heavy", "semibold", "demi"))


def _wh(w: dict) -> float:
    return max(w["bottom"] - w["top"], 0.1)


def _bands(words: list[dict]) -> list[list[dict]]:
    """Group words whose vertical centres agree into horizontal bands (any x)."""
    ws = sorted(words, key=lambda w: (w["top"] + w["bottom"]) / 2)
    bands: list[list[dict]] = []
    band_cy: list[float] = []
    for w in ws:
        cy = (w["top"] + w["bottom"]) / 2
        if bands and abs(cy - band_cy[-1]) <= 0.5 * min(_wh(w), statistics.median(_wh(x) for x in bands[-1])):
            bands[-1].append(w)
            band_cy[-1] = statistics.median((x["top"] + x["bottom"]) / 2 for x in bands[-1])
        else:
            bands.append([w]); band_cy.append(cy)
    for b in bands:
        b.sort(key=lambda w: w["x0"])
    return bands


def _segments(band: list[dict], factor: float = 1.2) -> list[list[dict]]:
    segs: list[list[dict]] = []
    cur: list[dict] = []
    for w in band:
        if cur:
            size = min(float(w.get("size", _wh(w))), float(cur[-1].get("size", _wh(cur[-1]))))
            if w["x0"] - cur[-1]["x1"] > size * factor:
                segs.append(cur); cur = []
        cur.append(w)
    if cur:
        segs.append(cur)
    return segs


_NUM_TOKEN_RE = re.compile(r"^[-+(]?[\d.,]+%?\)?$")


def clean_grid(rows: list[list[str]]) -> list[list[str]]:
    """Drop rows and columns that are empty everywhere (artefacts of partial rulings)."""
    rows = [r for r in rows if any(r)]
    if not rows:
        return rows
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [c for c in range(width) if any(r[c] for r in rows)]
    return [[r[c] for c in keep] for r in rows]


def grid_is_broken(rows: list[list[str]]) -> bool:
    """A ruled grid whose cells hold several separate numbers did not really find the
    cell boundaries (rulings only on some rows/columns): better re-detect from text
    alignment."""
    cells = [c for r in rows for c in r if c]
    if not cells:
        return True
    merged = sum(1 for c in cells if sum(1 for t in c.split() if _NUM_TOKEN_RE.match(t)) >= 3)
    return merged / len(cells) > 0.15


def page_rotation(page, threshold: float = 0.6) -> int:
    """90 / -90 if most glyphs on the page are rotated (sideways page), else 0."""
    chars = [c for c in page.chars if c["text"].strip()]
    if len(chars) < 20:
        return 0
    rot = [c for c in chars if not c.get("upright", True) and c.get("matrix")]
    if len(rot) / len(chars) < threshold:
        return 0
    ccw = sum(1 for c in rot if c["matrix"][1] > 0)
    return 90 if ccw >= len(rot) / 2 else -90


def rotated_words(page, rotation: int) -> list[dict]:
    """Words of a sideways page, in a frame where its text reads left-to-right.

    90  (text reads bottom-to-top): x' = H - y, y' = x
    -90 (text reads top-to-bottom): x' = y,     y' = W - x
    """
    from pdfplumber.utils import extract_words

    W, H = float(page.width), float(page.height)
    chars = []
    for c in page.chars:
        if c.get("upright", True):
            continue
        d = dict(c)
        if rotation == 90:
            d.update(x0=H - c["bottom"], x1=H - c["top"], top=c["x0"], bottom=c["x1"])
        else:
            d.update(x0=c["top"], x1=c["bottom"], top=W - c["x1"], bottom=W - c["x0"])
        d["upright"] = True
        d["doctop"] = d["top"]
        # pdfminer reports a rotated glyph's "size" as its advance; the real font size
        # is the scale of its text matrix.
        if c.get("matrix"):
            a, b = c["matrix"][0], c["matrix"][1]
            d["size"] = (a * a + b * b) ** 0.5
        chars.append(d)
    chars.sort(key=lambda c: (round(c["top"]), c["x0"]))
    return extract_words(chars, extra_attrs=["size", "fontname"], x_tolerance=3, x_tolerance_ratio=0.15,
                         keep_blank_chars=False, use_text_flow=False)


def find_borderless_tables(words: list[dict], min_rows: int = 3, min_cols: int = 3) -> tuple[list, list[dict]]:
    """Tables without ruling lines, found from text alignment alone.

    1. Lines (bands) that split into >= ``min_cols`` segments at wide gaps are
       candidate rows; runs of >= ``min_rows`` such rows, vertically close, form a
       candidate table.
    2. Column spans = union of the segments' x-intervals across the candidate rows.
       The candidate is accepted only if the rows agree: most segments fall inside
       exactly one span, and no span is fed by a single row.
    3. The table grows up/down over adjacent lines whose segments all fit inside the
       spans (header rows, rows with empty cells).
    Returns ([(bbox, rows)], remaining words).
    """
    bands = _bands(words)
    segs = [_segments(b) for b in bands]
    size = [statistics.median(float(w.get("size", _wh(w))) for w in b) for b in bands]
    used: set[int] = set()
    tables = []
    i = 0
    while i < len(bands):
        if len(segs[i]) < min_cols:
            i += 1
            continue
        j = i
        while (j + 1 < len(bands) and len(segs[j + 1]) >= min_cols
               and bands[j + 1][0]["top"] - max(w["bottom"] for w in bands[j]) < 2.5 * size[j]):
            j += 1
        if j - i + 1 < min_rows:
            i = j + 1
            continue
        spans = _column_spans([s for k in range(i, j + 1) for s in segs[k]])
        if len(spans) < min_cols or not _rows_agree(segs[i:j + 1], spans):
            i = j + 1
            continue
        # grow over header / sparse rows that fit the column grid
        lo, hi = i, j
        while lo - 1 >= 0 and len(segs[lo - 1]) >= 2 and _fits(segs[lo - 1], spans) \
                and bands[lo][0]["top"] - max(w["bottom"] for w in bands[lo - 1]) < 2.5 * size[lo]:
            lo -= 1
        while hi + 1 < len(bands) and len(segs[hi + 1]) >= 2 and _fits(segs[hi + 1], spans) \
                and bands[hi + 1][0]["top"] - max(w["bottom"] for w in bands[hi]) < 2.5 * size[hi]:
            hi += 1
        rows = []
        for k in range(lo, hi + 1):
            row = [""] * len(spans)
            for seg in segs[k]:
                c = _span_of(seg, spans)
                row[c] = (row[c] + " " + " ".join(w["text"] for w in seg)).strip()
            rows.append(row)
            used.update(id(w) for w in bands[k])
        allw = [w for k in range(lo, hi + 1) for w in bands[k]]
        bbox = (min(w["x0"] for w in allw), min(w["top"] for w in allw), max(w["x1"] for w in allw), max(w["bottom"] for w in allw))
        prev = tables[-1] if tables else None
        if prev is not None and prev[2] == len(spans) and lo - prev[3] - 1 <= 3 and _spans_match(prev[4], spans) \
                and all(len(segs[k]) >= 1 and _fits(segs[k], spans) for k in range(prev[3] + 1, lo)):
            # same column grid, interrupted only by a few one-cell rows (category
            # headings inside the table): one table, not two
            mid = []
            for k in range(prev[3] + 1, lo):
                row = [""] * len(spans)
                for seg in segs[k]:
                    c = _span_of(seg, spans)
                    row[c] = (row[c] + " " + " ".join(w["text"] for w in seg)).strip()
                mid.append(row)
                used.update(id(w) for w in bands[k])
            pb = prev[0]
            bbox = (min(pb[0], bbox[0]), min(pb[1], bbox[1]), max(pb[2], bbox[2]), max(pb[3], bbox[3]))
            tables[-1] = (bbox, prev[1] + mid + rows, len(spans), hi, spans)
        else:
            tables.append((bbox, rows, len(spans), hi, spans))
        i = hi + 1
    return [(t[0], t[1]) for t in tables], [w for w in words if id(w) not in used]


def _spans_match(a, b, tol: float = 6.0) -> bool:
    """Two column grids describe the same table if most columns overlap."""
    hits = sum(1 for (x0, x1) in a if any(min(x1, y1) - max(x0, y0) > -tol for (y0, y1) in b))
    return hits >= 0.8 * max(len(a), len(b))


def _column_spans(seglist: list[list[dict]]) -> list[tuple[float, float]]:
    iv = sorted((s[0]["x0"], max(w["x1"] for w in s)) for s in seglist)
    spans: list[list[float]] = []
    for a, b in iv:
        if spans and a <= spans[-1][1] + 1:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return [(a, b) for a, b in spans]


def _span_of(seg: list[dict], spans) -> int:
    a, b = seg[0]["x0"], max(w["x1"] for w in seg)
    best = max(range(len(spans)), key=lambda k: min(b, spans[k][1]) - max(a, spans[k][0]))
    return best


def _fits(segs_row: list[list[dict]], spans) -> bool:
    hit = set()
    for seg in segs_row:
        a, b = seg[0]["x0"], max(w["x1"] for w in seg)
        inside = [k for k, (x0, x1) in enumerate(spans) if a >= x0 - 2 and b <= x1 + 2]
        if len(inside) != 1 or inside[0] in hit:
            return False
        hit.add(inside[0])
    return True


def _rows_agree(rows: list[list[list[dict]]], spans) -> bool:
    fed = [0] * len(spans)
    ok = total = 0
    for segs_row in rows:
        seen = set()
        for seg in segs_row:
            total += 1
            k = _span_of(seg, spans)
            if k not in seen:
                ok += 1
                seen.add(k)
                fed[k] += 1
    return total > 0 and ok / total >= 0.9 and min(fed) >= 2


def words_to_lines(words: list[dict]) -> list[Line]:
    """Group words into lines.

    1. Bands: words whose vertical centres agree (within half a word height) share a
       band, regardless of arrival order (OCR boxes jitter; text order may be odd).
    2. Within a band, words are sorted left to right and the band is split wherever
       the horizontal gap is wider than a generous inter-word space: a column gutter
       or a separate text region.
    """
    if not words:
        return []
    def h(w):
        return max(w["bottom"] - w["top"], 0.1)
    bands = _bands(words)
    lines: list[Line] = []
    band_segments: list[list[list[dict]]] = []
    for band in bands:
        band.sort(key=lambda w: w["x0"])
        segs: list[list[dict]] = []
        cur: list[dict] = []
        for w in band:
            if cur:
                size = min(float(w.get("size", h(w))), float(cur[-1].get("size", h(cur[-1]))))
                gap = w["x0"] - cur[-1]["x1"]
                # a section number ("1", "2.3", "A.") stays with the heading text after its tab
                numbering = len(cur) == 1 and SECTION_NUM_RE.match(cur[0]["text"]) and gap < size * 6
                if gap > size * 1.2 and not numbering:
                    segs.append(cur); cur = []
            cur.append(w)
        if cur:
            segs.append(cur)
        band_segments.append(segs)
    _join_label_value_rows(band_segments)
    for segs in band_segments:
        for seg in segs:
            lines.append(_make_line(seg))
    lines = _attach_bullet_glyphs(lines)
    for ln in lines:
        ln.words.sort(key=lambda w: w["x0"])
    return lines


TOC_LEADER_RE = re.compile(r"(?:\.\s?){4,}\s*[\divxlc]*\s*$", re.I)
SECTION_NUM_RE = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2}){0,4}\.?|[A-Z]\.|[IVX]{1,4}\.)$")


def _join_label_value_rows(band_segments: list[list[list[dict]]], min_rows: int = 3) -> None:
    """Abbreviation lists, glossaries and form-like "Label   value" rows: two text
    segments per line, a short label at a consistent x and the value at another
    consistent x. When at least ``min_rows`` lines share that geometry, each label is
    joined with its value into one line (so XY-cut does not read them as columns).
    Body text in two columns is unaffected: its left segments are long."""
    key_rows: dict[tuple[int, int], list[int]] = {}
    for i, segs in enumerate(band_segments):
        if len(segs) == 2 and len(segs[0]) <= 4 and not BULLETS_RE.match(segs[0][0]["text"] + " "):
            key = (round(segs[0][0]["x0"] / 3), round(segs[1][0]["x0"] / 3))
            key_rows.setdefault(key, []).append(i)
    for rows in key_rows.values():
        # only *consecutive* label/value lines form a list; isolated coincidences (a short
        # last line of a left-column paragraph beside right-column text) do not
        runs: list[list[int]] = []
        for i in rows:
            if runs and i - runs[-1][-1] == 1:
                runs[-1].append(i)
            else:
                runs.append([i])
        for run in runs:
            if len(run) < min_rows:
                continue
            for i in run:
                joined = band_segments[i][0] + band_segments[i][1]
                for w in joined:
                    w["_row"] = True  # each label/value row is its own block
                band_segments[i] = [joined]


def _make_line(ws: list[dict]) -> Line:
    sizes = [float(w.get("size", w["bottom"] - w["top"])) for w in ws]
    return Line(list(ws), min(w["x0"] for w in ws), max(w["x1"] for w in ws), min(w["top"] for w in ws),
                max(w["bottom"] for w in ws), statistics.median(sizes) if len(sizes) > 2 else max(sizes),
                all(_is_bold(w.get("fontname", "")) for w in ws))


def _attach_bullet_glyphs(lines: list[Line]) -> list[Line]:
    """Word processors often draw a bullet as a separate glyph in a symbol font whose
    box sits higher or lower than the text. Re-attach such lone glyphs to the text
    line immediately to their right."""
    out = []
    for ln in lines:
        if len(ln.words) == 1 and BULLETS_RE.match(ln.words[0]["text"] + " "):
            cy = (ln.top + ln.bottom) / 2
            best = None
            for other in lines:
                if other is ln or other.x0 < ln.x1 - 1 or other.x0 - ln.x1 > 40:
                    continue
                dist = abs((other.top + other.bottom) / 2 - cy)
                if dist <= max(other.size, ln.size) and (best is None or dist < best[0]):
                    best = (dist, other)
            if best is not None:
                tgt = best[1]
                tgt.words.insert(0, dict(ln.words[0], text="\u2022"))
                tgt.x0 = min(tgt.x0, ln.x0)
                continue
        out.append(ln)
    return out


def _starts_item(text: str) -> bool:
    return bool(BULLETS_RE.match(text) or ORDERED_BULLET_RE.match(text))


_SENT_END = (".", "!", "?", ":", '."', ".)")


def typical_line_gap(lines: list[Line]) -> float:
    """Median whitespace between consecutive lines of running text (the leading)."""
    lines = sorted(lines, key=lambda l: (l.top, l.x0))
    gaps = []
    for i, ln in enumerate(lines):
        for nxt in lines[i + 1:i + 4]:
            if min(ln.x1, nxt.x1) - max(ln.x0, nxt.x0) > 0 and abs(ln.size - nxt.size) < 0.6:
                g = nxt.top - ln.bottom
                if 0 <= g < 2 * ln.size:
                    gaps.append(g)
                break
    if not gaps:
        return 2.0
    gaps.sort()
    return gaps[len(gaps) // 3]  # lower tercile: in-paragraph gaps outnumber paragraph gaps


def _text_start(line: Line) -> float:
    """x where the text of a bulleted line starts (after the bullet glyph)."""
    if len(line.words) > 1 and _starts_item(line.text):
        return line.words[1]["x0"]
    return line.x0


def _continues(block: Block, ln: Line, lead: float) -> bool:
    last = block.lines[-1]
    gap = ln.top - last.bottom
    if gap > lead * 1.5 + 1.0 or gap < -0.5 * last.size:
        return False
    if abs(ln.size - last.size) > 0.6 or ln.bold != last.bold or _starts_item(ln.text):
        return False
    if ln.words[0].get("_row") or last.words[0].get("_row"):
        return False
    first = block.lines[0]
    # Deliberate line break test: if the next line's first word would have fitted in
    # the space left on this line, the writer ended the block here.
    right_edge = max(l.x1 for l in block.lines)
    first_word = ln.words[0]["x1"] - ln.words[0]["x0"]
    short_last = len(block.lines) > 1 and right_edge - last.x1 > first_word + 0.5 * ln.size
    if _starts_item(first.text):
        # Wrapped lines of a list item either hang at the item's text indent, or (common
        # in numbered references) return to the item's own left edge.
        if ln.x0 >= _text_start(first) - 3:
            return True
        return ln.x0 >= first.x0 - 3 and not short_last and not (len(block.lines) == 1 and _ends_short(first, ln))
    if last.text.rstrip().endswith(_SENT_END):
        indented = ln.x0 > min(l.x0 for l in block.lines) + 0.8 * ln.size
        if short_last or indented:
            return False
    return True


def _ends_short(line: Line, nxt: Line) -> bool:
    """Single-line item whose text stops well before the next line's right edge."""
    return nxt.x1 - line.x1 > (nxt.words[0]["x1"] - nxt.words[0]["x0"]) + 0.5 * nxt.size


def lines_to_blocks(lines: list[Line], lead: Optional[float] = None) -> list[Block]:
    lines = sorted(lines, key=lambda l: (l.top, l.x0))
    lead = typical_line_gap(lines) if lead is None else lead
    blocks: list[Block] = []
    # Each line joins the open block directly above it (horizontally overlapping),
    # so lines in side-by-side columns never merge.
    open_blocks: list[Block] = []
    for ln in lines:
        target = None
        for b in reversed(open_blocks):
            last = b.lines[-1]
            if min(last.x1, ln.x1) - max(last.x0, ln.x0) <= 0 and min(b.x1, ln.x1) - max(b.x0, ln.x0) <= 0:
                continue
            if _continues(b, ln, lead):
                target = b
            break
        if target is None:
            target = Block()
            blocks.append(target)
            open_blocks = [b for b in open_blocks if min(b.x1, ln.x1) - max(b.x0, ln.x0) <= 0] + [target]
        target.lines.append(ln)
    return blocks


def xy_cut(blocks: list[Block], depth: int = 0) -> list[Block]:
    """Recursive XY-cut: split by the widest horizontal whitespace band first (rows),
    otherwise by a vertical whitespace gutter (columns), and recurse."""
    if len(blocks) <= 1 or depth > 30:
        return blocks

    def gaps(intervals):
        intervals = sorted(intervals)
        best = None
        reach = intervals[0][1]
        for lo, hi in intervals[1:]:
            if lo > reach:
                g = (lo - reach, (reach + lo) / 2)
                best = g if best is None or g[0] > best[0] else best
            reach = max(reach, hi)
        return best

    hgap = gaps([(b.top, b.bottom) for b in blocks])
    vgap = gaps([(b.x0, b.x1) for b in blocks])
    # A clear vertical gutter means columns: read the left one fully first. Blocks
    # spanning the page (titles, wide tables) remove the gutter at this level, so the
    # horizontal cut separates them first and the columns are split one level down.
    if vgap and vgap[0] >= 8:
        cut = vgap[1]
        left = [b for b in blocks if b.x1 <= cut]
        right = [b for b in blocks if b.x1 > cut]
        return xy_cut(left, depth + 1) + xy_cut(right, depth + 1)
    if hgap:
        cut = hgap[1]
        upper = [b for b in blocks if b.bottom <= cut]
        lower = [b for b in blocks if b.bottom > cut]
        return xy_cut(upper, depth + 1) + xy_cut(lower, depth + 1)
    return sorted(blocks, key=lambda b: (b.top, b.x0))


# Glyph codes pdfminer cannot map to Unicode come out as "(cid:N)". The common ones
# in Standard/WinAnsi encodings are bullets; anything else is dropped.
_CID_RE = re.compile(r"\(cid:(\d+)\)")
_CID_BULLETS = {"127", "128", "149", "183", "108", "8226", "2022"}


def _fix_cids(text: str) -> str:
    return _CID_RE.sub(lambda m: "\u2022" if m.group(1) in _CID_BULLETS else "", text)


def _list_depth(block: Block, blocks: list[Block]) -> int:
    """Nesting level = rank of this item's left edge among the distinct left edges of
    list items in the same column (blocks that overlap it horizontally)."""
    col = [b for b in blocks if min(b.x1, block.x1) - max(b.x0, block.x0) > 0 and _starts_item(b.lines[0].text)]
    edges: list[float] = []
    for x in sorted(b.x0 for b in col):
        if not edges or x - edges[-1] > 4:
            edges.append(x)
    return next((i for i, e in enumerate(edges) if abs(block.x0 - e) <= 4), 0)


_FLOW_TYPES = ("NarrativeText", "UncategorizedText", "Text")


def merge_continuations(elements: list[Element]) -> list[Element]:
    """Re-join a paragraph broken by a column or page break: the next body element
    starts higher up (new column) or on a later page, the previous one does not end
    a sentence, and the next one starts in lower case."""
    out: list[Element] = []
    prev: Optional[Element] = None
    for el in elements:
        if isinstance(el, (Header, Footer, PageBreak)):
            out.append(el)
            continue
        if getattr(el, "_rotated", False) or getattr(prev, "_rotated", False):
            out.append(el)
            prev = el
            continue
        if (prev is not None and prev.category in _FLOW_TYPES and el.category in _FLOW_TYPES
                and el.text[:1].islower() and not prev.text.rstrip().endswith(_SENT_END)):
            pc, ec = prev.metadata.coordinates, el.metadata.coordinates
            jumped = (el.metadata.page_number != prev.metadata.page_number
                      or (pc is not None and ec is not None and ec.points[0][1] < pc.points[0][1]))
            if jumped:
                merged = classify_text(prev.text + " " + el.text, prev.metadata)
                if merged is not None:
                    out[out.index(prev)] = merged
                    prev = merged
                    continue
        out.append(el)
        prev = el
    return out


def _vertical_runs(page) -> list[tuple[tuple, str]]:
    """Rebuild rotated text from individual glyphs.

    Glyphs are grouped into runs sharing an x position, then ordered along the run:
    bottom-to-top when the glyph matrix says the text is turned counter-clockwise
    (b > 0), else top-to-bottom. Spaces are inserted where the along-run gap exceeds
    15% of the font size.
    """
    chars = [c for c in page.chars if not c.get("upright", True) and c["text"].strip()]
    runs: list[list[dict]] = []
    for c in sorted(chars, key=lambda c: (round(c["x0"]), c["top"])):
        if runs and abs(runs[-1][-1]["x0"] - c["x0"]) < 3 and c["top"] - runs[-1][-1]["bottom"] < 20:
            runs[-1].append(c)
        else:
            runs.append([c])
    out = []
    for r in runs:
        ccw = sum(1 for c in r if c.get("matrix") and c["matrix"][1] > 0) > len(r) / 2
        r.sort(key=lambda c: c["top"], reverse=ccw)
        parts = [r[0]["text"]]
        for prev, c in zip(r, r[1:]):
            gap = (prev["top"] - c["bottom"]) if ccw else (c["top"] - prev["bottom"])
            parts.append((" " if gap > 0.15 * float(c.get("size", 10)) else "") + c["text"])
        text = clean_extra_whitespace(clean_ligatures(_fix_cids("".join(parts))))
        if text:
            out.append(((min(c["x0"] for c in r), min(c["top"] for c in r), max(c["x1"] for c in r),
                         max(c["bottom"] for c in r)), text))
    return out


CAPTION_RE = re.compile(r"^(?:fig\.?|figure|abb\.?|abbildung|table|tab\.|tabelle|chart|exhibit|plate)\s*[\dIVX]+[a-z]?\s*[.:|\u2013\u2014-]", re.I)


def _page_images(page, min_side: float = 40.0) -> list[tuple]:
    """Bounding boxes of embedded images large enough to be figures (not rules/logos dots)."""
    out = []
    for im in page.images:
        x0, top, x1, bottom = im["x0"], im["top"], im["x1"], im["bottom"]
        if x1 - x0 >= min_side and bottom - top >= min_side:
            box = (max(0, x0), max(0, top), min(float(page.width), x1), min(float(page.height), bottom))
            if box not in out:
                out.append(box)
    return out


def link_captions(elements: list[Element], max_gap: float = 72.0) -> None:
    """Point each FigureCaption's parent_id at the nearest image on the same page
    (vertically adjacent, horizontally overlapping)."""
    images = [e for e in elements if isinstance(e, Image) and e.metadata.coordinates is not None]
    for cap in elements:
        if not isinstance(cap, FigureCaption) or cap.metadata.coordinates is None:
            continue
        (cx0, ctop), (_, cbot), (cx1, _), _ = cap.metadata.coordinates.points
        best = None
        for im in images:
            if im.metadata.page_number != cap.metadata.page_number:
                continue
            (ix0, itop), (_, ibot), (ix1, _), _ = im.metadata.coordinates.points
            if min(cx1, ix1) - max(cx0, ix0) <= 0:
                continue
            gap = ctop - ibot if ctop >= ibot else itop - cbot
            if -2 <= gap <= max_gap and (best is None or gap < best[0]):
                best = (gap, im)
        if best is not None:
            cap.metadata.parent_id = best[1].id


def repair_pdf(data: bytes) -> bytes:
    """Re-write a PDF with pypdf (lenient parser), keeping only the pages."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(data), strict=False)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _norm_furniture(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower()).strip()


# --------------------------------------------------------------- main
def partition_pdf(
    filename: Optional[str] = None,
    file: Optional[FileLike] = None,
    metadata_filename: Optional[str] = None,
    languages: Optional[list[str]] = None,
    include_page_breaks: bool = False,
    infer_table_structure: bool = True,
    include_headers_footers: bool = True,
    unique_element_ids: bool = False,
    margin_ratio: float = 0.08,
    repeat_band: float = 0.15,
    ocr: str = "auto",
    ocr_languages: str = "eng",
    strategy: Optional[str] = None,
    **_: object,
) -> list[Element]:
    """Partition a PDF.

    ``strategy`` accepts unstructured's values for drop-in compatibility:
    ``"fast"`` (text layer only, never OCR), ``"ocr_only"`` (OCR every page),
    ``"hi_res"`` / ``"auto"`` (text layer, OCR for image-only pages). ``ocr``
    (``"auto"|"always"|"never"``) is the native spelling of the same switch.
    """
    if strategy is not None:
        ocr = {"fast": "never", "ocr_only": "always"}.get(strategy, "auto")
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise ImportError("PDF support needs `pip install pdfplumber`.") from exc

    data = read_bytes(filename, file)
    base = base_metadata(filename, "application/pdf", metadata_filename)

    pages: list[dict] = []
    any_ocr = False
    try:
        pdf_ctx = pdfplumber.open(io.BytesIO(data))
        pdf_ctx.pages  # noqa: B018 - forces the page tree to be parsed now
    except Exception:
        # Malformed files (broken trailer/Info dict, bad xref): rebuild with pypdf,
        # which is more forgiving, dropping the document-info dictionary.
        data = repair_pdf(data)
        pdf_ctx = pdfplumber.open(io.BytesIO(data))
    with pdf_ctx as pdf:
        info_mod = (pdf.metadata or {}).get("ModDate")
        for pno, page in enumerate(pdf.pages, start=1):
            tables = []
            if infer_table_structure:
                for t in page.find_tables():
                    rows = clean_grid([[clean_extra_whitespace(c or "") for c in r] for r in t.extract()])
                    if rows and len(rows[0]) > 1 and not grid_is_broken(rows):
                        tables.append((t.bbox, rows))
            any_ocr_page = False
            # A gap wider than 15% of the font size separates words. A fixed tolerance
            # (pdfplumber's default 3pt) glues words in tightly-set justified text.
            words = page.extract_words(extra_attrs=["size", "fontname"], keep_blank_chars=False, use_text_flow=False,
                                       x_tolerance=3, x_tolerance_ratio=0.15)
            rotation = page_rotation(page)
            page_w, page_h = float(page.width), float(page.height)
            if rotation:
                # Sideways page (landscape table printed on a portrait page): rotate every
                # glyph back into a normal reading frame and run the usual pipeline there.
                # Coordinates in metadata are then in that rotated frame.
                words = rotated_words(page, rotation)
                tables = []  # ruled-grid cells would be read sideways; rely on alignment
                page_w, page_h = page_h, page_w
            if ocr != "never" and (ocr == "always" or page_needs_ocr(page, words)) and tesseract_available():
                words, ocr_tables = ocr_page(page, lang=ocr_languages)
                tables = ocr_tables if infer_table_structure else []
                any_ocr = True
                any_ocr_page = True

            def in_table(w):
                return any(x0 - 1 <= w["x0"] and w["x1"] <= x1 + 1 and top - 1 <= w["top"] and w["bottom"] <= bottom + 1
                           for (x0, top, x1, bottom), _ in tables)

            words = [dict(w, text=clean_ligatures(_fix_cids(w["text"]))) for w in words if not in_table(w)]
            words = [w for w in words if w["text"].strip()]
            if infer_table_structure:
                upright_words = [w for w in words if w.get("upright", True)]
                text_tables, rest = find_borderless_tables(upright_words)
                if text_tables:
                    keep = {id(w) for w in rest}
                    words = [w for w in words if not w.get("upright", True) or id(w) in keep]
                tables = tables + text_tables
            # Rotated text (arXiv side stamps, rotated table headers, axis labels) would
            # otherwise become one-character "lines"; keep it out of the main flow and
            # emit each vertical run as a single element.
            vertical = [w for w in words if not w.get("upright", True)]
            words = [w for w in words if w.get("upright", True)]
            blocks = lines_to_blocks(words_to_lines(words))
            pages.append({"no": pno, "w": page_w, "h": page_h, "blocks": blocks, "tables": tables,
                          "vertical": _vertical_runs(page) if vertical and not rotation else [],
                          "images": [] if any_ocr_page else _page_images(page)})
            page.close()  # release pdfminer's cached layout objects: keeps memory flat on long documents
    if base.last_modified is None and isinstance(info_mod, str):
        base.last_modified = info_mod

    # --- body font size & heading size ranks (document-wide) ---
    size_hist: Counter = Counter()
    for pg in pages:
        for b in pg["blocks"]:
            for ln in b.lines:
                size_hist[round(ln.size * 2) / 2] += len(ln.text)
    body_size = size_hist.most_common(1)[0][0] if size_hist else 10.0

    def heading_candidate(b: Block) -> bool:
        text = b.text
        n = len(text.split())
        if not text or n > 20 or text.endswith(".") or _starts_item(text):
            return False
        if URL_RE.search(text) or EMAIL_IN_TEXT_RE.search(text) or TOC_LEADER_RE.search(text) or is_page_number(text):
            return False
        if b.size >= body_size * 1.15:
            return True
        return b.bold and len(b.lines) <= 2 and n <= 12

    # Heading levels: cluster candidate sizes (OCR size estimates are noisier, so
    # use a wider tolerance); bigger cluster = higher level. At equal size, an
    # emphasised heading outranks a plain one.
    tol = 0.75 if any_ocr else 0.3
    cand_sizes = sorted({round(b.size * 2) / 2 for pg in pages for b in pg["blocks"] if heading_candidate(b)}, reverse=True)
    level_of: dict[float, int] = {}
    level, anchor = -1, None
    for sz in cand_sizes:
        if anchor is None or anchor - sz > tol:
            level += 1
            anchor = sz
        level_of[sz] = level

    # --- repeating header/footer text ---
    furniture: Counter = Counter()
    for pg in pages:
        seen = set()
        for b in pg["blocks"]:
            if b.bottom < pg["h"] * repeat_band or b.top > pg["h"] * (1 - repeat_band):
                seen.add(_norm_furniture(b.text))
        furniture.update(seen)
    min_repeat = max(2, int(len(pages) * 0.5))

    elements: list[Element] = []
    for pg in pages:
        pno = pg["no"]

        def md(bbox) -> ElementMetadata:
            m = base.copy()
            m.page_number = pno
            m.coordinates = Coordinates.from_bbox(*bbox, width=pg["w"], height=pg["h"])
            return m

        items: list[tuple[float, float, Element]] = []
        table_blocks = [TableBlock(x0, top, x1, bottom, rows) for (x0, top, x1, bottom), rows in pg["tables"]]
        for b in xy_cut(pg["blocks"] + table_blocks):
            if isinstance(b, TableBlock):
                tel = Table(rows_to_text(b.rows), md((b.x0, b.top, b.x1, b.bottom)))
                tel.metadata.text_as_html = rows_to_html(b.rows)
                items.append((b.top, b.x0, tel))
                continue
            text = b.text
            if not text:
                continue
            bbox = (b.x0, b.top, b.x1, b.bottom)
            in_top = b.bottom < pg["h"] * margin_ratio
            in_bottom = b.top > pg["h"] * (1 - margin_ratio)
            # Running heads can sit lower than the tight margin band; exact repetition
            # across most pages is strong evidence, so allow a wider band for it.
            rep_top = b.bottom < pg["h"] * repeat_band
            rep_bottom = b.top > pg["h"] * (1 - repeat_band)
            repeated = furniture[_norm_furniture(text)] >= min_repeat and len(text.split()) <= 25
            # Furniture: margin text that repeats across pages, is a page number, or (for
            # documents too short to show repetition) is a short line set smaller than body.
            small_margin_note = b.size < body_size - 0.9 and len(text.split()) <= 12 and len(b.lines) == 1
            if ((rep_top or rep_bottom) and repeated) or ((in_top or in_bottom) and (is_page_number(text) or small_margin_note)):
                in_top = in_top or rep_top
                if include_headers_footers:
                    cls = Header if in_top else Footer
                    items.append((b.top, b.x0, cls(text, md(bbox))))
                continue
            is_row = bool(b.lines[0].words[0].get("_row"))
            if is_row:
                items.append((b.top, b.x0, Text(text, md(bbox))))
                continue
            if heading_candidate(b):
                m = md(bbox); m.category_depth = level_of[round(b.size * 2) / 2]
                items.append((b.top, b.x0, Title(text, m)))
                continue
            if CAPTION_RE.match(text):
                items.append((b.top, b.x0, FigureCaption(text, md(bbox))))
                continue
            el = classify_text(text, md(bbox))
            if el is None:
                continue
            if isinstance(el, ListItem):
                el.metadata.category_depth = _list_depth(b, pg["blocks"])
            items.append((b.top, b.x0, el))

        for bbox in pg.get("images", []):
            items.append((bbox[1], bbox[0], Image("", md(bbox))))
        for bbox, text in pg.get("vertical", []):
            vel = UncategorizedText(text, md(bbox))
            vel._rotated = True  # type: ignore[attr-defined]
            items.append((bbox[1], bbox[0], vel))
        ordered = [e for _, _, e in items]
        elements.extend(ordered)
        if pno < len(pages):
            pb = PageBreak("", base.copy()); pb.metadata.page_number = pno
            elements.append(pb)
    elements = merge_continuations(elements)
    elements = finalize(elements, base, languages, include_page_breaks, unique_element_ids)
    link_captions(elements)
    return elements

