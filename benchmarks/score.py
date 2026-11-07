"""Score parser outputs against ground truth.

    python benchmarks/score.py --data DIR results/docparse.json results/unstructured.json [--md report.md]

Metrics (per document, averaged per format; higher is better unless noted):

text_sim       normalised Levenshtein similarity of all body text, in reading order, vs truth
block_recall   share of truth blocks (titles, paragraphs, list items) recovered as one
               element (fuzzy ratio >= 90) - penalises merged/split blocks
type_acc       of recovered blocks, share with the right category
               (Title / NarrativeText / ListItem; paragraphs may also be Text/UncategorizedText
               in the lenient variant type_acc_lenient)
depth_acc      of recovered titles, share with the right relative heading level
parent_acc     of recovered blocks that have a parent in the truth, share whose parent_id
               points at the element matched to the true parent
order          Kendall-style share of recovered block pairs in the right order
table_cells    share of truth table cells found at the right (row, col) in a Table's
               text_as_html (0 if the table is missed or has no HTML)
furniture_leak share of running header/footer strings that leak into body elements (lower is better)
sec/doc        wall time per document (lower is better)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from collections import defaultdict
from html.parser import HTMLParser

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

FURNITURE = {"Header", "Footer", "PageBreak", "PageNumber"}
PARA_LENIENT = {"NarrativeText", "Text", "UncategorizedText"}


def norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("ﬁ", "fi").replace("ﬂ", "fl")
    s = re.sub(r"^\s*[•–●▪\-\*o·]\s+", "", s)
    return " ".join(s.split()).strip()


class _Rows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cell, self.in_cell = [], [], False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self.in_cell, self.cell = True, []
            if not self.rows:
                self.rows.append([])

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.in_cell:
            self.rows[-1].append(norm("".join(self.cell)))
            self.in_cell = False

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)


def html_rows(h: str) -> list[list[str]]:
    p = _Rows()
    p.feed(h or "")
    return [r for r in p.rows if any(r)]


def truth_parents(blocks):
    parents, titles, lists = [], [], []
    for i, b in enumerate(blocks):
        if b["type"] == "Title":
            while titles and blocks[titles[-1]]["depth"] >= b["depth"]:
                titles.pop()
            parents.append(titles[-1] if titles else None)
            titles.append(i); lists = []
        elif b["type"] == "ListItem":
            d = b["depth"]
            del lists[d:]
            parents.append(lists[d - 1] if d > 0 and len(lists) >= d else (titles[-1] if titles else None))
            lists.append(i)
        else:
            parents.append(titles[-1] if titles else None); lists = []
    return parents


def table_text(rows):
    return "\n".join(" ".join(r) for r in rows)


def score_doc(truth: dict, elements: list[dict]) -> dict:
    blocks = truth["blocks"]
    body = [e for e in elements if e["type"] not in FURNITURE]
    m: dict = {}

    # --- text similarity (reading order sensitive) ---
    gt_text = norm(" ".join(b["text"] if b["type"] != "Table" else table_text(b["rows"]) for b in blocks))
    pr_text = norm(" ".join(norm(e.get("text", "")) for e in body))
    m["text_sim"] = Levenshtein.normalized_similarity(gt_text, pr_text)

    # --- block matching (one-to-one, best ratio first) ---
    ptexts = [norm(e.get("text", "")) for e in body]
    cands = []
    for i, b in enumerate(blocks):
        if b["type"] == "Table":
            continue
        t = norm(b["text"])
        for j, pt in enumerate(ptexts):
            if abs(len(pt) - len(t)) > 0.3 * max(len(t), 1) + 5:
                continue
            r = fuzz.ratio(t, pt)
            if r >= 90:
                cands.append((r, i, j))
    cands.sort(reverse=True)
    match, used = {}, set()
    for r, i, j in cands:
        if i not in match and j not in used:
            match[i] = j; used.add(j)
    text_blocks = [i for i, b in enumerate(blocks) if b["type"] != "Table"]
    m["block_recall"] = len(match) / len(text_blocks)

    expected = {"Title": {"Title"}, "Paragraph": {"NarrativeText"}, "ListItem": {"ListItem"}}
    ok = [body[j]["type"] in expected[blocks[i]["type"]] for i, j in match.items()]
    ok_len = [body[j]["type"] in (PARA_LENIENT if blocks[i]["type"] == "Paragraph" else expected[blocks[i]["type"]]) for i, j in match.items()]
    m["type_acc"] = sum(ok) / len(ok) if ok else 0.0
    m["type_acc_lenient"] = sum(ok_len) / len(ok_len) if ok_len else 0.0

    titles = [(i, j) for i, j in match.items() if blocks[i]["type"] == "Title" and body[j]["type"] == "Title"]
    depths = [body[j]["metadata"].get("category_depth") for _, j in titles]
    depths = [d if d is not None else 0 for d in depths]
    base = min(depths) if depths else 0
    dok = [blocks[i]["depth"] == d - base for (i, _), d in zip(titles, depths)]
    m["depth_acc"] = sum(dok) / len(dok) if dok else 0.0

    tparents = truth_parents(blocks)
    pok = []
    for i, j in match.items():
        tp = tparents[i]
        if tp is None:
            continue
        expected_pid = body[match[tp]]["element_id"] if tp in match else None
        pok.append(expected_pid is not None and body[j]["metadata"].get("parent_id") == expected_pid)
    m["parent_acc"] = sum(pok) / len(pok) if pok else 0.0

    seq = [match[i] for i in sorted(match)]
    pairs = conc = 0
    for a in range(len(seq)):
        for b in range(a + 1, len(seq)):
            pairs += 1; conc += seq[a] < seq[b]
    m["order"] = conc / pairs if pairs else (1.0 if match else 0.0)

    # --- tables ---
    gt_tables = [b["rows"] for b in blocks if b["type"] == "Table"]
    pred_tables = [html_rows(e["metadata"].get("text_as_html", "")) for e in body if e["type"] in ("Table", "TableChunk")]
    if gt_tables:
        scores, used_t = [], set()
        for rows in gt_tables:
            gt = [[norm(c) for c in r] for r in rows]
            best, best_k = 0.0, None
            for k, pr in enumerate(pred_tables):
                if k in used_t:
                    continue
                hit = sum(1 for r, row in enumerate(gt) for c, v in enumerate(row)
                          if r < len(pr) and c < len(pr[r]) and pr[r][c] == v)
                s = hit / sum(len(r) for r in gt)
                if s > best:
                    best, best_k = s, k
            if best_k is not None:
                used_t.add(best_k)
            scores.append(best)
        m["table_cells"] = sum(scores) / len(scores)

    # --- furniture leakage ---
    furn = [norm(truth["header"]), norm(truth["footer"])]
    leaks = sum(1 for f in furn for t in ptexts if f and f in t)
    m["furniture_leak"] = min(1.0, leaks / len(furn))
    return m


METRICS = ["text_sim", "block_recall", "type_acc", "type_acc_lenient", "depth_acc", "parent_acc", "order", "table_cells", "furniture_leak", "sec/doc", "errors"]
LOWER_BETTER = {"furniture_leak", "sec/doc", "errors"}


def aggregate(data_dir, result_file):
    with open(result_file) as f:
        results = json.load(f)
    per_fmt = defaultdict(lambda: defaultdict(list))
    for key, r in results.items():
        fmt, doc = key.split("/")
        with open(os.path.join(data_dir, f"{doc}.truth.json")) as f:
            truth = json.load(f)
        sc = score_doc(truth, r["elements"])
        for k, v in sc.items():
            per_fmt[fmt][k].append(v)
        per_fmt[fmt]["sec/doc"].append(r["seconds"])
        per_fmt[fmt]["errors"].append(1 if r["error"] else 0)
    out = {}
    for fmt, d in per_fmt.items():
        out[fmt] = {k: (sum(v) if k == "errors" else statistics.mean(v)) for k, v in d.items()}
    allm = defaultdict(list)
    for fmt, d in per_fmt.items():
        for k, v in d.items():
            allm[k].extend(v)
    out["ALL"] = {k: (sum(v) if k == "errors" else statistics.mean(v)) for k, v in allm.items()}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--md")
    ap.add_argument("results", nargs="+")
    a = ap.parse_args()
    names = [os.path.splitext(os.path.basename(r))[0] for r in a.results]
    aggs = [aggregate(a.data, r) for r in a.results]
    fmts = [f for f in ["docx", "html", "md", "pdf", "pdf2col", "scanned", "ALL"] if f in aggs[0]]
    lines = []
    for fmt in fmts:
        lines.append(f"\n### {fmt}\n")
        lines.append("| metric | " + " | ".join(names) + " |")
        lines.append("|---|" + "---|" * len(names))
        for k in METRICS:
            vals = [ag[fmt].get(k) for ag in aggs]
            if all(v is None for v in vals):
                continue
            nums = [v for v in vals if v is not None]
            best = (min if k in LOWER_BETTER else max)(nums)
            cells = []
            for v in vals:
                if v is None:
                    cells.append("–")
                    continue
                s = f"{v:.3f}" if k not in ("errors",) else f"{int(v)}"
                cells.append(f"**{s}**" if v == best and len(set(nums)) > 1 else s)
            lines.append(f"| {k} | " + " | ".join(cells) + " |")
    report = "\n".join(lines)
    print(report)
    if a.md:
        with open(a.md, "w") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
