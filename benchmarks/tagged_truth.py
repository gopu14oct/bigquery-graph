"""Ground truth for *real* PDFs from their accessibility tags.

Tagged PDFs (exported by Word, InDesign, Acrobat...) carry a structure tree written
by the authoring tool: headings (H1-H6), paragraphs (P), lists (L/LI), tables
(Table/TR/TD), and the logical reading order. Every glyph is linked to its tag by a
marked-content id (MCID); page furniture is marked as an Artifact. Neither parser
reads these tags, so they are an independent answer key built by the document's author's
software, not by us.

Output uses the same ``truth`` format as the synthetic benchmark, so ``score.py``
scores real documents with exactly the same metrics.

    python benchmarks/tagged_truth.py --corpus /tmp/real/corpus --out /tmp/real/truth \\
        nccn-137p geobase-uml-19p breast-cancer-76p

Caveat: tags are only as good as the authoring tool's export. InDesign documents
often tag headings as plain P (then heading metrics are not measurable), and some
exporters tag a whole text frame as one P. ``--report`` prints tag statistics so the
answer key can be sanity-checked before use.
"""

from __future__ import annotations

import argparse
import collections
import json
import os

import pdfplumber

HEADINGS = {f"H{i}": i - 1 for i in range(1, 7)}
BLOCKS = {"P", "Caption", "BlockQuote", "Note", "Title", "Code"} | set(HEADINGS)


def _norm(s: str) -> str:
    return " ".join(s.replace("­", "").split())


class Truth:
    def __init__(self, pdf):
        self.pdf = pdf
        self.page_chars: dict[int, dict[int, list]] = {}

    def chars_for(self, page_number: int) -> dict[int, list]:
        if page_number not in self.page_chars:
            by_mcid: dict[int, list] = collections.defaultdict(list)
            page = self.pdf.pages[page_number - 1]
            for c in page.chars:
                if c.get("mcid") is not None:
                    by_mcid[c["mcid"]].append(c)
            self.page_chars[page_number] = by_mcid
            page.close()
        return self.page_chars[page_number]

    def _collect(self, node, page, out: dict) -> None:
        page = node.get("page_number") or page
        if node.get("mcids") and page:
            chars = self.chars_for(page)
            for m in node["mcids"]:
                out.setdefault(page, []).extend(chars.get(m, []))
        for ch in node.get("children", []):
            self._collect(ch, page, out)

    def text(self, node, page=None) -> str:
        """Text of a node's glyphs (its own and its descendants'), in *visual* order:
        page by page, line by line, left to right. Tag-tree order is not used because
        exporters sometimes emit inline runs (bold terms, links) out of order."""
        by_page: dict = {}
        self._collect(node, page, by_page)
        parts = []
        for pg in sorted(by_page):
            cs = by_page[pg]
            if not cs:
                continue
            cs = sorted(cs, key=lambda c: ((c["top"] + c["bottom"]) / 2, c["x0"]))
            lines: list[list] = []
            for c in cs:
                cy = (c["top"] + c["bottom"]) / 2
                if lines and abs(cy - lines[-1][0]) <= 0.4 * c["size"]:
                    lines[-1][1].append(c)
                else:
                    lines.append([cy, [c]])
            for _, lc in lines:
                lc.sort(key=lambda c: c["x0"])
                buf = []
                for prev, c in zip([None] + lc[:-1], lc):
                    if prev is not None and c["x0"] - prev["x1"] > 0.15 * c["size"]:
                        buf.append(" ")
                    buf.append(c["text"])
                parts.append("".join(buf))
        return _norm(" ".join(parts))

    def first_page(self, node):
        if node.get("page_number"):
            return node["page_number"]
        for ch in node.get("children", []):
            p = self.first_page(ch)
            if p:
                return p
        return None


def build(path: str) -> dict:
    pdf = pdfplumber.open(path)
    t = Truth(pdf)
    blocks: list[dict] = []

    def walk(node, list_depth=-1):
        typ = node.get("type")
        if typ == "Artifact":
            return
        if typ == "Table":
            rows = []
            for tr in _descend(node, "TR"):
                cells = [t.text(td) for td in tr.get("children", []) if td.get("type") in ("TD", "TH")]
                if any(cells):
                    rows.append(cells)
            if rows:
                blocks.append({"type": "Table", "rows": rows, "page": t.first_page(node)})
            return
        if typ == "L":
            for ch in node.get("children", []):
                walk(ch, list_depth + 1)
            return
        if typ == "LI":
            body = [c for c in node.get("children", []) if c.get("type") != "L"]
            text = t.text({"children": [c for c in body if c.get("type") != "Lbl"]}, t.first_page(node))
            if text:
                blocks.append({"type": "ListItem", "depth": max(list_depth, 0), "text": text, "page": t.first_page(node)})
            for c in node.get("children", []):
                if c.get("type") == "L":
                    walk(c, list_depth)
                elif c.get("type") == "LBody":
                    for g in c.get("children", []):
                        if g.get("type") == "L":
                            walk(g, list_depth)
            return
        if typ in BLOCKS:
            text = t.text(node)
            if text:
                if typ in HEADINGS:
                    blocks.append({"type": "Title", "depth": HEADINGS[typ], "text": text, "page": t.first_page(node)})
                else:
                    blocks.append({"type": "Paragraph", "text": text, "page": t.first_page(node)})
            return
        for ch in node.get("children", []):  # Document, Part, Sect, Art, Div, Span...
            walk(ch, list_depth)

    for root in pdf.structure_tree:
        walk(root)
    # Drop exact duplicate texts (repeated boilerplate) so one-to-one matching is unambiguous.
    seen = collections.Counter(b.get("text") for b in blocks if b["type"] != "Table")
    blocks = [b for b in blocks if b["type"] == "Table" or seen[b["text"]] == 1]
    # Very short fragments (single symbols, page refs) are not meaningful blocks.
    blocks = [b for b in blocks if b["type"] == "Table" or len(b["text"]) >= 3]
    return {"blocks": blocks, "header": "", "footer": "", "source": "pdf-structure-tags"}


def _descend(node, typ):
    for ch in node.get("children", []):
        if ch.get("type") == typ:
            yield ch
        else:
            yield from _descend(ch, typ)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("docs", nargs="+")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for name in a.docs:
        truth = build(os.path.join(a.corpus, name + ".pdf"))
        with open(os.path.join(a.out, name + ".truth.json"), "w") as f:
            json.dump(truth, f, indent=1)
        c = collections.Counter(b["type"] for b in truth["blocks"])
        depths = collections.Counter(b.get("depth") for b in truth["blocks"] if b["type"] == "Title")
        print(f"{name}: {dict(c)} title depths {dict(depths)}")


if __name__ == "__main__":
    main()
