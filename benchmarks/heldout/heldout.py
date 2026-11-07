"""Held-out evaluation (see PROTOCOL.md). Corpus selection, answer keys and scoring.

    python benchmarks/heldout/heldout.py select --pdfjs /home/user/mozilla/pdf.js/test/pdfs \\
        --icdar /home/user/camelot-dev/camelot/tests/files/tabula/icdar2013-dataset --out /tmp/heldout
    python benchmarks/realworld.py run --out /tmp/heldout/h1 --parser docparse --timeout 120     # likewise h2, h3
    VENV/python benchmarks/realworld.py run --out /tmp/heldout/h1 --parser unstructured --python VENV/python --timeout 120
    python benchmarks/heldout/heldout.py score --out /tmp/heldout --md benchmarks/heldout/RESULTS.md
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from realworld import bow_scores, words  # noqa: E402
from score import html_rows, norm, score_doc  # noqa: E402
from tables_camelot import cell_score  # noqa: E402

PARSERS = ["docparse", "unstructured"]


def pdftotext(path: str) -> str:
    try:
        return subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"], capture_output=True, timeout=120).stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return ""


def npages(path: str) -> int:
    try:
        out = subprocess.run(["pdfinfo", path], capture_output=True, text=True, timeout=60).stdout
    except subprocess.TimeoutExpired:
        return 0
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def add(out_dir: str, name: str, src: str, manifest: dict, extra: dict | None = None) -> None:
    os.makedirs(os.path.join(out_dir, "corpus"), exist_ok=True)
    dst = os.path.join(out_dir, "corpus", name + ".pdf")
    shutil.copy(src, dst)
    ref = pdftotext(dst)
    with open(os.path.join(out_dir, "corpus", name + ".ref.txt"), "w") as f:
        f.write(ref)
    try:
        fonts = subprocess.run(["pdffonts", dst], capture_output=True, text=True, timeout=60).stdout.splitlines()[2:]
    except subprocess.TimeoutExpired:
        fonts = ["?"]
    kind = "digital" if fonts else "scanned"  # no fonts at all = no text layer
    manifest[name] = {"kind": kind, "pages": npages(dst), "source": src, **(extra or {})}


# ------------------------------------------------------------------ select
def select(pdfjs: str, icdar: str, out: str) -> None:
    files = sorted(glob.glob(os.path.join(pdfjs, "*.pdf")))
    # H1 robustness: seeded random sample
    h1 = os.path.join(out, "h1"); m1: dict = {}
    for src in random.Random(2026).sample(files, 300):
        add(h1, safe(os.path.splitext(os.path.basename(src))[0]), src, m1)
    json.dump(m1, open(os.path.join(h1, "manifest.json"), "w"), indent=1)
    # H2 structure (Amendment 1): candidates chosen by rule across third-party test corpora
    from tagged_truth import build as build_truth  # noqa: E402

    h2 = os.path.join(out, "h2"); m2: dict = {}
    os.makedirs(os.path.join(h2, "truth"), exist_ok=True)
    with open(os.path.join(HERE, "h2_sources.json")) as f:
        candidates = json.load(f)
    for src, _n in candidates:
        name = safe(os.path.splitext(os.path.basename(src))[0])
        truth = build_truth(src)
        add(h2, name, src, m2, {"truth_blocks": sum(1 for b in truth["blocks"] if b["type"] != "Table")})
        json.dump(truth, open(os.path.join(h2, "truth", name + ".truth.json"), "w"))
    json.dump(m2, open(os.path.join(h2, "manifest.json"), "w"), indent=1)
    skipped: list = []
    # H3 tables: ICDAR 2013 minus us-030
    h3 = os.path.join(out, "h3"); m3: dict = {}
    os.makedirs(os.path.join(h3, "truth"), exist_ok=True)
    for src in sorted(glob.glob(os.path.join(icdar, "*", "*.pdf"))):
        name = os.path.splitext(os.path.basename(src))[0]
        if name == "us-030":
            continue
        strx = src[:-4] + "-str.xml"
        if not os.path.exists(strx):
            continue
        add(h3, name, src, m3)
        json.dump(icdar_tables(strx), open(os.path.join(h3, "truth", name + ".tables.json"), "w"))
    json.dump(m3, open(os.path.join(h3, "manifest.json"), "w"), indent=1)
    print(f"H1 {len(m1)} docs, H2 {len(m2)} docs (skipped {len(skipped)}), H3 {len(m3)} docs")


def icdar_tables(path: str) -> list[dict]:
    """ICDAR 2013 -str.xml -> [{page, grid}] (one per table region; spanning cells
    placed at their start position)."""
    root = ET.parse(path).getroot()
    out = []
    for table in root.iter("table"):
        for region in table.iter("region"):
            cells = []
            for c in region.iter("cell"):
                content = c.find("content")
                txt = norm(content.text or "") if content is not None else ""
                cells.append((int(c.get("start-row")), int(c.get("start-col")), txt))
            if not cells:
                continue
            nr = max(r for r, _, _ in cells) + 1
            nc = max(c for _, c, _ in cells) + 1
            grid = [["" for _ in range(nc)] for _ in range(nr)]
            for r, c, t in cells:
                grid[r][c] = (grid[r][c] + " " + t).strip()
            out.append({"page": int(region.get("page")), "grid": grid})
    return out


# ------------------------------------------------------------------ metrics
def adjacency(grid: list[list[str]]) -> collections.Counter:
    """ICDAR 2013 structure metric: relations between each non-empty cell and its
    nearest non-empty neighbour to the right and below."""
    rel: collections.Counter = collections.Counter()
    nr = len(grid)
    for r in range(nr):
        for c, v in enumerate(grid[r]):
            if not v:
                continue
            for c2 in range(c + 1, len(grid[r])):
                if grid[r][c2]:
                    rel[(norm(v), norm(grid[r][c2]), "H")] += 1
                    break
            for r2 in range(r + 1, nr):
                if c < len(grid[r2]) and grid[r2][c]:
                    rel[(norm(v), norm(grid[r2][c]), "V")] += 1
                    break
    return rel


def adjacency_f1(gt: list[list[str]], pred: list[list[str]]) -> float:
    a, b = adjacency(gt), adjacency(pred)
    inter = sum((a & b).values())
    if not inter:
        return 0.0
    p, r = inter / sum(b.values()), inter / sum(a.values())
    return 2 * p * r / (p + r)


def bootstrap(values: list[float], n: int = 1000, seed: int = 7) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def load(out_dir: str, parser: str, name: str) -> dict:
    p = os.path.join(out_dir, "results", parser, name + ".json")
    if not os.path.exists(p):
        return {"elements": [], "status": "missing", "seconds": 0, "peak_mb": None}
    with open(p) as f:
        return json.load(f)


def fmt(vals: list[float]) -> str:
    if not vals:
        return "–"
    lo, hi = bootstrap(vals)
    return f"{sum(vals) / len(vals):.3f} [{lo:.3f}, {hi:.3f}]"


# ------------------------------------------------------------------ score
def score(out: str, md: str | None) -> None:
    L: list[str] = []
    # H1
    h1 = os.path.join(out, "h1"); m1 = json.load(open(os.path.join(h1, "manifest.json")))
    L += ["## H1 Robustness: 300 random pdf.js corpus PDFs\n",
          "| metric | " + " | ".join(PARSERS) + " |", "|---|" + "---|" * len(PARSERS)]
    stats = {}
    for p in PARSERS:
        crash = timeout = silent = 0; f1s = []; secs = pages = 0
        for name, info in m1.items():
            res = load(h1, p, name)
            st = res.get("status", "ok")
            ref = words(open(os.path.join(h1, "corpus", name + ".ref.txt")).read())
            hyp = words(" ".join(e["text"] for e in res["elements"]))
            if st.startswith("crash") or st == "missing":
                crash += 1
            elif st.startswith("timeout"):
                timeout += 1
            if sum(ref.values()) >= 50:
                if sum((ref & hyp).values()) < 0.1 * sum(ref.values()) and st == "ok":
                    silent += 1
                f1s.append(bow_scores(ref, hyp)[2] if st == "ok" else 0.0)
            if st == "ok":
                secs += res["seconds"]; pages += info["pages"]
        stats[p] = (crash, timeout, silent, f1s, secs / max(1, pages))
    n = len(m1)
    L.append("| crashes | " + " | ".join(f"{stats[p][0]} / {n}" for p in PARSERS) + " |")
    L.append("| timeouts (>120 s) | " + " | ".join(f"{stats[p][1]} / {n}" for p in PARSERS) + " |")
    L.append("| silent failures (<10% of pdftotext words) | " + " | ".join(f"{stats[p][2]} / {len(stats[p][3])}" for p in PARSERS) + " |")
    L.append("| word F1 vs pdftotext (docs with ≥50 words), mean [95% CI] | " + " | ".join(fmt(stats[p][3]) for p in PARSERS) + " |")
    L.append("| seconds per page (successful docs) | " + " | ".join(f"{stats[p][4]:.3f}" for p in PARSERS) + " |")
    # H2
    h2 = os.path.join(out, "h2"); m2 = json.load(open(os.path.join(h2, "manifest.json")))
    metrics = ["text_sim", "block_recall", "type_acc", "type_acc_lenient", "depth_acc", "parent_acc", "order"]
    per = {p: collections.defaultdict(list) for p in PARSERS}
    weights = []
    for name in m2:
        truth = json.load(open(os.path.join(h2, "truth", name + ".truth.json")))
        has_titles = any(b["type"] == "Title" for b in truth["blocks"])
        weights.append(len(truth["blocks"]))
        for p in PARSERS:
            res = load(h2, p, name)
            sc = score_doc(truth, res["elements"]) if res.get("status", "ok") == "ok" else {m: 0.0 for m in metrics}
            for mm in metrics:
                if mm == "depth_acc" and not has_titles:
                    continue
                per[p][mm].append(sc.get(mm, 0.0))
    L += [f"\n## H2 Structure: {len(m2)} tagged pdf.js PDFs ({sum(weights)} tagged blocks)\n",
          "Macro average over documents, mean [95% bootstrap CI]. depth_acc only on documents with heading tags.\n",
          "| metric | " + " | ".join(PARSERS) + " |", "|---|" + "---|" * len(PARSERS)]
    for mm in metrics:
        L.append(f"| {mm} | " + " | ".join(fmt(per[p][mm]) for p in PARSERS) + " |")
    L.append("\n<details><summary>per document</summary>\n")
    L.append("| document | pages | truth blocks | " + " | ".join(f"{p} recall / type / order" for p in PARSERS) + " |")
    L.append("|---|---|---|" + "---|" * len(PARSERS))
    for i, (name, info) in enumerate(m2.items()):
        cells = [f"{per[p]['block_recall'][i]:.2f} / {per[p]['type_acc'][i]:.2f} / {per[p]['order'][i]:.2f}" for p in PARSERS]
        L.append(f"| {name} | {info['pages']} | {info['truth_blocks']} | " + " | ".join(cells) + " |")
    L.append("\n</details>")
    # H3
    h3 = os.path.join(out, "h3"); m3 = json.load(open(os.path.join(h3, "manifest.json")))
    adj = {p: [] for p in PARSERS}; grid = {p: [] for p in PARSERS}; found = {p: 0 for p in PARSERS}; ntab = 0
    for name in m3:
        gts = json.load(open(os.path.join(h3, "truth", name + ".tables.json")))
        for p in PARSERS:
            res = load(h3, p, name)
            preds = [(e["metadata"].get("page_number"), html_rows(e["metadata"].get("text_as_html", "")))
                     for e in res["elements"] if e["type"] in ("Table", "TableChunk") and e["metadata"].get("text_as_html")]
            for gt in gts:
                same = [g for pg, g in preds if pg == gt["page"]] or [g for _, g in preds]
                a = max((adjacency_f1(gt["grid"], g) for g in same), default=0.0)
                c = max((cell_score(gt["grid"], g) for g in same), default=0.0)
                adj[p].append(a); grid[p].append(c); found[p] += a > 0
        ntab += len(gts)
    L += [f"\n## H3 Tables: ICDAR 2013 competition, {len(m3)} documents, {ntab} ground-truth tables\n",
          "| metric | " + " | ".join(PARSERS) + " |", "|---|" + "---|" * len(PARSERS),
          "| adjacency-relation F1 (ICDAR 2013 metric), mean [95% CI] | " + " | ".join(fmt(adj[p]) for p in PARSERS) + " |",
          "| cell-grid accuracy, mean [95% CI] | " + " | ".join(fmt(grid[p]) for p in PARSERS) + " |",
          "| tables detected (any correct relation) | " + " | ".join(f"{found[p]} / {ntab}" for p in PARSERS) + " |"]
    text = "\n".join(L)
    print(text)
    if md:
        with open(md, "w") as f:
            f.write(text + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["select", "score"])
    ap.add_argument("--pdfjs", default="/home/user/mozilla/pdf.js/test/pdfs")
    ap.add_argument("--icdar", default="/home/user/camelot-dev/camelot/tests/files/tabula/icdar2013-dataset")
    ap.add_argument("--out", required=True)
    ap.add_argument("--md")
    a = ap.parse_args()
    if a.cmd == "select":
        select(a.pdfjs, a.icdar, a.out)
    else:
        score(a.out, a.md)


if __name__ == "__main__":
    main()
