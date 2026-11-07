"""Real-world PDF benchmark (no hand-made ground truth).

Real documents have no answer key, so this measures what *can* be measured objectively:

* robustness  - crashes / timeouts per document
* scale       - seconds per page and peak memory (each parse runs in its own process)
* text completeness - bag-of-words recall and precision against an *independent*
                      extractor (poppler's ``pdftotext``) for born-digital PDFs
* OCR accuracy - for scanned copies we make ourselves, the original text layer *is*
                 the ground truth (bag-of-words F1, order-insensitive)
* structure stats - elements/page, titles, tables, list items, header/footer,
                    and tiny fragments (a sign of broken lines/paragraphs)

Reading order and table quality on real documents are judged by manual inspection
(see REALWORLD.md), not by these numbers.

    python benchmarks/realworld.py build  --out /tmp/real            # assemble corpus
    python benchmarks/realworld.py run    --out /tmp/real --parser docparse --python python3
    python benchmarks/realworld.py run    --out /tmp/real --parser unstructured --python /tmp/uvenv/bin/python
    python benchmarks/realworld.py report --out /tmp/real
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
U = "/home/user/unstructured-io/unstructured/example-docs"
P = "/home/user/py-pdf/sample-files"

# name -> (source path, description)
CORPUS = {
    "DA-619p": (f"{U}/pdf/DA-619p.pdf", "619-page OpenOffice book: TOC with dot leaders, chapters"),
    "irs-400p": (f"{U}/pdf/pdf2image-memory-error-test-400p.pdf", "400-page XPP/Distiller government publication, 117 images"),
    "nccn-137p": ("/tmp/claude-0/realpdf/x/pypdf-7.0.0/resources/issue-604.pdf", "137-page InDesign clinical guideline: flowcharts, footnotes, 148 images"),
    "geotopo-117p": (f"{P}/009-pdflatex-geotopo/GeoTopo.pdf", "117-page German LaTeX textbook: maths, figures"),
    "breast-cancer-76p": (f"{U}/pdf/failure-after-repair.pdf", "76-page InDesign patient brochure: sidebars, images"),
    "geobase-uml-19p": ("/tmp/claude-0/realpdf/x/pypdf-7.0.0/resources/GeoBase_NHNC1_Data_Model_UML_EN.pdf", "19-page Word spec: UML tables"),
    "layout-parser-16p": (f"{U}/pdf/layout-parser-paper.pdf", "16-page LaTeX paper: figures, tables, code"),
    "multi-column-13p": (f"{U}/pdf/multi-column.pdf", "13-page two-column ACL paper"),
    "fr-olap-12p": (f"{U}/language-docs/fr_olap.pdf", "12-page French academic paper"),
    "conr-11p": (f"{U}/pdf/invalid-pdf-structure-pdfminer-entire-doc.pdf", "11-page two-column paper, broken PDF structure, 23 images"),
    "latex-multicol-3p": (f"{P}/026-latex-multicolumn/multicolumn.pdf", "3-page LaTeX multicol"),
    "korean-tables-1p": (f"{U}/pdf/korean-text-with-tables.pdf", "Korean text with tables"),
    "table-merged-cells-1p": (f"{U}/pdf/table-multi-row-column-cells.pdf", "table with row/col spans"),
    "chevron-1p": (f"{U}/pdf/chevron-page.pdf", "image-heavy corporate page"),
    "vicksburg-ocr-6p": ("/tmp/claude-0/realpdf/x/pypdf-7.0.0/resources/Seige_of_Vicksburg_Sample_OCR.pdf", "scanned book pages with an OCR text layer"),
}
# born-digital docs we rasterise to make scanned copies whose ground truth is the text layer
SCAN_FROM = ["layout-parser-16p", "multi-column-13p", "geobase-uml-19p", "breast-cancer-76p"]
SCAN_MAX_PAGES = 12
SCAN_DPI = 200

WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def words(text: str) -> collections.Counter:
    return collections.Counter(w.lower() for w in WORD_RE.findall(text) if len(w) > 1 or w.isdigit())


def bow_scores(ref: collections.Counter, hyp: collections.Counter) -> tuple[float, float, float]:
    inter = sum((ref & hyp).values())
    r = inter / max(1, sum(ref.values()))
    p = inter / max(1, sum(hyp.values()))
    f = 2 * p * r / (p + r) if p + r else 0.0
    return r, p, f


def npages(path: str) -> int:
    out = subprocess.run(["pdfinfo", path], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


# ------------------------------------------------------------------ build
def build(out: str) -> None:
    corpus = os.path.join(out, "corpus")
    os.makedirs(corpus, exist_ok=True)
    manifest = {}
    for name, (src, desc) in CORPUS.items():
        if not os.path.exists(src):
            print("missing", src, file=sys.stderr)
            continue
        dst = os.path.join(corpus, name + ".pdf")
        shutil.copy(src, dst)
        manifest[name] = {"kind": "digital", "desc": desc, "pages": npages(dst)}
        ref = subprocess.run(["pdftotext", "-enc", "UTF-8", dst, "-"], capture_output=True).stdout.decode("utf-8", "replace")
        with open(os.path.join(corpus, name + ".ref.txt"), "w") as f:
            f.write(ref)
    from PIL import Image

    for name in SCAN_FROM:
        src = os.path.join(corpus, name + ".pdf")
        n = min(SCAN_MAX_PAGES, manifest[name]["pages"])
        sname = f"{name.rsplit('-', 1)[0]}-scan{n}p"
        dst = os.path.join(corpus, sname + ".pdf")
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["pdftoppm", "-r", str(SCAN_DPI), "-gray", "-png", "-l", str(n), src, os.path.join(td, "p")], check=True)
            imgs = [Image.open(os.path.join(td, f)).convert("L") for f in sorted(os.listdir(td))]
            imgs[0].save(dst, save_all=True, append_images=imgs[1:], resolution=SCAN_DPI)
        ref = subprocess.run(["pdftotext", "-enc", "UTF-8", "-l", str(n), src, "-"], capture_output=True).stdout.decode("utf-8", "replace")
        with open(os.path.join(corpus, sname + ".ref.txt"), "w") as f:
            f.write(ref)
        manifest[sname] = {"kind": "scanned", "desc": f"{SCAN_DPI} dpi grayscale scan of the first {n} pages of {name}", "pages": n}
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(json.dumps(manifest, indent=1))


# ------------------------------------------------------------------ run (one parser)
WORKER = r'''
import json, os, resource, sys, time
os.environ["UNSTRUCTURED_DISABLE_NOTICE"] = "1"
parser, path, kind, out = sys.argv[1:5]
t0 = time.perf_counter()
if parser == "docparse":
    sys.path.insert(0, sys.argv[5])
    from docparse import partition
    els = partition(filename=path)
else:
    from unstructured.partition.pdf import partition_pdf
    els = partition_pdf(filename=path, strategy="ocr_only" if kind == "scanned" else "fast", languages=["eng"])
secs = time.perf_counter() - t0
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
with open(out, "w") as f:
    json.dump({"seconds": secs, "peak_mb": rss, "elements": [e.to_dict() for e in els]}, f)
'''


def run(out: str, parser: str, python: str, timeout: int, only: list[str] | None) -> None:
    with open(os.path.join(out, "manifest.json")) as f:
        manifest = json.load(f)
    resdir = os.path.join(out, "results", parser)
    os.makedirs(resdir, exist_ok=True)
    for name, info in manifest.items():
        if only and name not in only:
            continue
        path = os.path.join(out, "corpus", name + ".pdf")
        dst = os.path.join(resdir, name + ".json")
        t0 = time.perf_counter()
        try:
            p = subprocess.run([python, "-c", WORKER, parser, path, info["kind"], dst, os.path.dirname(HERE)],
                               capture_output=True, text=True, timeout=timeout)
            status = "ok" if p.returncode == 0 else f"crash: {(p.stderr.strip().splitlines() or ['?'])[-1][:200]}"
        except subprocess.TimeoutExpired:
            status = f"timeout >{timeout}s"
        if status != "ok":
            with open(dst, "w") as f:
                json.dump({"seconds": time.perf_counter() - t0, "peak_mb": None, "elements": [], "status": status}, f)
        print(f"{parser:12s} {name:24s} {info['pages']:4d}p {status} {time.perf_counter() - t0:7.1f}s", file=sys.stderr)


# ------------------------------------------------------------------ report
def doc_metrics(res: dict, ref_text: str, pages: int) -> dict:
    els = res["elements"]
    body = [e for e in els if e["type"] not in ("Header", "Footer", "PageBreak", "PageNumber")]
    hyp = words(" ".join(e["text"] for e in els))
    ref = words(ref_text)
    r, p, f1 = bow_scores(ref, hyp)
    types = collections.Counter(e["type"] for e in els)
    frag = sum(1 for e in body if len(e["text"].strip()) <= 3)
    return {
        "status": res.get("status", "ok"),
        "sec/page": res["seconds"] / max(1, pages),
        "peak_mb": res["peak_mb"],
        "word_recall": r, "word_precision": p, "word_f1": f1,
        "elements/page": len(els) / max(1, pages),
        "titles": types.get("Title", 0),
        "tables": types.get("Table", 0),
        "list_items": types.get("ListItem", 0),
        "headers+footers": types.get("Header", 0) + types.get("Footer", 0),
        "images": types.get("Image", 0),
        "fragments%": 100 * frag / max(1, len(body)),
    }


def report(out: str, parsers: list[str], md_path: str | None) -> None:
    with open(os.path.join(out, "manifest.json")) as f:
        manifest = json.load(f)
    rows = []
    for name, info in manifest.items():
        with open(os.path.join(out, "corpus", name + ".ref.txt")) as f:
            ref_text = f.read()
        for parser in parsers:
            path = os.path.join(out, "results", parser, name + ".json")
            if not os.path.exists(path):
                continue
            with open(path) as f:
                res = json.load(f)
            rows.append((name, info, parser, doc_metrics(res, ref_text, info["pages"])))
    cols = ["status", "sec/page", "peak_mb", "word_recall", "word_precision", "word_f1", "elements/page", "titles",
            "tables", "list_items", "headers+footers", "fragments%"]
    lines = ["| document | pages | parser | " + " | ".join(cols) + " |", "|---|---|---|" + "---|" * len(cols)]
    for name, info, parser, m in rows:
        cells = []
        for c in cols:
            v = m[c]
            cells.append("–" if v is None else (f"{v:.3f}" if isinstance(v, float) and c.startswith("word") else
                                                f"{v:.2f}" if isinstance(v, float) else str(v)))
        lines.append(f"| {name} | {info['pages']} | {parser} | " + " | ".join(cells) + " |")
    # aggregates
    lines.append("")
    lines.append("| parser | docs ok | total pages | total sec | sec/page | median word F1 (digital) | mean word F1 (scanned) | max peak MB |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for parser in parsers:
        mine = [(n, i, m) for n, i, p, m in rows if p == parser]
        ok = [x for x in mine if x[2]["status"] == "ok"]
        pages = sum(i["pages"] for _, i, _ in ok)
        secs = sum(m["sec/page"] * i["pages"] for _, i, m in ok)
        dig = sorted(m["word_f1"] for _, i, m in mine if i["kind"] == "digital")
        scn = [m["word_f1"] for _, i, m in mine if i["kind"] == "scanned"]
        peak = max((m["peak_mb"] or 0) for _, _, m in mine) if mine else 0
        lines.append(f"| {parser} | {len(ok)}/{len(mine)} | {pages} | {secs:.0f} | {secs / max(1, pages):.3f} | "
                     f"{(dig[len(dig) // 2] if dig else 0):.3f} | {sum(scn) / max(1, len(scn)):.3f} | {peak:.0f} |")
    text = "\n".join(lines)
    print(text)
    if md_path:
        with open(md_path, "w") as f:
            f.write(text + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "run", "report"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--parser")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--md")
    a = ap.parse_args()
    if a.cmd == "build":
        build(a.out)
    elif a.cmd == "run":
        run(a.out, a.parser, a.python, a.timeout, a.only)
    else:
        report(a.out, ["docparse", "unstructured"], a.md)


if __name__ == "__main__":
    main()
