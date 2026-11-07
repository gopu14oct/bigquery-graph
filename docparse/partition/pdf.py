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
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from docparse.cleaners import clean_extra_whitespace, clean_ligatures, dehyphenate
from docparse.elements import (
    Coordinates,
    Element,
    ElementMetadata,
    Footer,
    Header,
    ListItem,
    PageBreak,
    Table,
    Title,
)
from docparse.partition.common import FileLike, base_metadata, classify_text, finalize, read_bytes
from docparse.partition.html import rows_to_html, rows_to_text
from docparse.text_type import BULLETS_RE, ORDERED_BULLET_RE, is_page_number


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


def words_to_lines(words: list[dict]) -> list[Line]:
    words = sorted(words, key=lambda w: (round(w["top"], 1), w["x0"]))
    lines: list[Line] = []
    for w in words:
        size = float(w.get("size", w["bottom"] - w["top"]))
        placed = False
        # look back a few lines only - lines are produced roughly top-to-bottom
        for ln in reversed(lines[-4:]):
            overlap = min(ln.bottom, w["bottom"]) - max(ln.top, w["top"])
            height = min(ln.bottom - ln.top, w["bottom"] - w["top"]) or 1
            gap = w["x0"] - ln.x1
            if overlap / height > 0.5 and -1 < gap < max(size, ln.size) * 3:
                ln.words.append(w)
                ln.x1 = max(ln.x1, w["x1"]); ln.top = min(ln.top, w["top"]); ln.bottom = max(ln.bottom, w["bottom"])
                ln.size = max(ln.size, size)
                ln.bold = ln.bold and _is_bold(w.get("fontname", ""))
                placed = True
                break
        if not placed:
            lines.append(Line([w], w["x0"], w["x1"], w["top"], w["bottom"], size, _is_bold(w.get("fontname", ""))))
    lines = _attach_bullet_glyphs(lines)
    for ln in lines:
        ln.words.sort(key=lambda w: w["x0"])
    return lines


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


def lines_to_blocks(lines: list[Line]) -> list[Block]:
    lines = sorted(lines, key=lambda l: (l.top, l.x0))
    blocks: list[Block] = []
    # Process per horizontal band so side-by-side columns don't merge: assign each line
    # to the open block it vertically continues and horizontally overlaps.
    open_blocks: list[Block] = []
    for ln in lines:
        target = None
        for b in reversed(open_blocks):
            last = b.lines[-1]
            h_overlap = min(b.x1, ln.x1) - max(b.x0, ln.x0)
            if h_overlap <= 0:
                continue
            gap = ln.top - last.bottom
            line_h = max(last.bottom - last.top, 1)
            same_font = abs(ln.size - last.size) <= 0.6 and ln.bold == last.bold
            if gap < line_h * 0.9 and same_font and not _starts_item(ln.text):
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
    if hgap and (not vgap or hgap[0] >= 2 or vgap[0] < 8):
        cut = hgap[1]
        upper = [b for b in blocks if b.bottom <= cut]
        lower = [b for b in blocks if b.bottom > cut]
        return xy_cut(upper, depth + 1) + xy_cut(lower, depth + 1)
    if vgap and vgap[0] >= 8:
        cut = vgap[1]
        left = [b for b in blocks if b.x1 <= cut]
        right = [b for b in blocks if b.x1 > cut]
        return xy_cut(left, depth + 1) + xy_cut(right, depth + 1)
    return sorted(blocks, key=lambda b: (b.top, b.x0))


# Glyph codes pdfminer cannot map to Unicode come out as "(cid:N)". The common ones
# in Standard/WinAnsi encodings are bullets; anything else is dropped.
_CID_RE = re.compile(r"\(cid:(\d+)\)")
_CID_BULLETS = {"127", "128", "149", "183", "108", "8226", "2022"}


def _fix_cids(text: str) -> str:
    return _CID_RE.sub(lambda m: "\u2022" if m.group(1) in _CID_BULLETS else "", text)


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
    **_: object,
) -> list[Element]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise ImportError("PDF support needs `pip install pdfplumber`.") from exc

    data = read_bytes(filename, file)
    base = base_metadata(filename, "application/pdf", metadata_filename)

    pages: list[dict] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        info_mod = (pdf.metadata or {}).get("ModDate")
        for pno, page in enumerate(pdf.pages, start=1):
            tables = []
            if infer_table_structure:
                for t in page.find_tables():
                    rows = [[clean_extra_whitespace(c or "") for c in r] for r in t.extract()]
                    rows = [r for r in rows if any(r)]
                    if rows and len(rows[0]) > 1:
                        tables.append((t.bbox, rows))
            words = page.extract_words(extra_attrs=["size", "fontname"], keep_blank_chars=False, use_text_flow=False)

            def in_table(w):
                return any(x0 - 1 <= w["x0"] and w["x1"] <= x1 + 1 and top - 1 <= w["top"] and w["bottom"] <= bottom + 1
                           for (x0, top, x1, bottom), _ in tables)

            words = [dict(w, text=clean_ligatures(_fix_cids(w["text"]))) for w in words if not in_table(w)]
            words = [w for w in words if w["text"].strip()]
            blocks = lines_to_blocks(words_to_lines(words))
            pages.append({"no": pno, "w": float(page.width), "h": float(page.height), "blocks": blocks, "tables": tables})
    if base.last_modified is None and isinstance(info_mod, str):
        base.last_modified = info_mod

    # --- body font size & heading size ranks (document-wide) ---
    size_hist: Counter = Counter()
    for pg in pages:
        for b in pg["blocks"]:
            for ln in b.lines:
                size_hist[round(ln.size * 2) / 2] += len(ln.text)
    body_size = size_hist.most_common(1)[0][0] if size_hist else 10.0
    heading_sizes = sorted({round(b.size * 2) / 2 for pg in pages for b in pg["blocks"]
                            if b.size > body_size * 1.15 and len(b.text) < 200}, reverse=True)

    # --- repeating header/footer text ---
    furniture: Counter = Counter()
    for pg in pages:
        seen = set()
        for b in pg["blocks"]:
            if b.bottom < pg["h"] * margin_ratio or b.top > pg["h"] * (1 - margin_ratio):
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
        for b in xy_cut(pg["blocks"]):
            text = b.text
            if not text:
                continue
            bbox = (b.x0, b.top, b.x1, b.bottom)
            in_top = b.bottom < pg["h"] * margin_ratio
            in_bottom = b.top > pg["h"] * (1 - margin_ratio)
            if (in_top or in_bottom) and (furniture[_norm_furniture(text)] >= min_repeat or is_page_number(text)):
                if include_headers_footers:
                    cls = Header if in_top else Footer
                    items.append((b.top, b.x0, cls(text, md(bbox))))
                continue
            size = round(b.size * 2) / 2
            if size in heading_sizes and len(text.split()) <= 20:
                m = md(bbox); m.category_depth = heading_sizes.index(size)
                items.append((b.top, b.x0, Title(text, m)))
                continue
            if b.bold and len(b.lines) <= 2 and len(text.split()) <= 12 and not text.endswith("."):
                m = md(bbox); m.category_depth = len(heading_sizes)
                items.append((b.top, b.x0, Title(text, m)))
                continue
            el = classify_text(text, md(bbox))
            if el is None:
                continue
            if isinstance(el, ListItem):
                indent = b.x0 - min(bb.x0 for bb in pg["blocks"])
                el.metadata.category_depth = int(indent // 18) if indent > 0 else 0
            items.append((b.top, b.x0, el))

        # splice tables into reading order: before the first text block that starts below them
        for (x0, top, x1, bottom), rows in pg["tables"]:
            tel = Table(rows_to_text(rows), md((x0, top, x1, bottom)))
            tel.metadata.text_as_html = rows_to_html(rows)
            pos = next((i for i, (t, _, e) in enumerate(items) if t > top and not isinstance(e, Header)), len(items))
            items.insert(pos, (top, x0, tel))
        ordered = [e for _, _, e in items]
        elements.extend(ordered)
        if pno < len(pages):
            pb = PageBreak("", base.copy()); pb.metadata.page_number = pno
            elements.append(pb)
    return finalize(elements, base, languages, include_page_breaks, unique_element_ids)

