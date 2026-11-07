"""Run one parser over a benchmark split and dump its elements + timings.

    python  benchmarks/run_parser.py --parser docparse     --data DIR --out results/docparse.json
    VENV/python benchmarks/run_parser.py --parser unstructured --data DIR --out results/unstructured.json

Unstructured is run with the best strategy that works offline:
``fast`` for born-digital PDFs and ``ocr_only`` for scanned ones (``hi_res`` needs
model weights from huggingface.co). Everything else uses its defaults.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import traceback

os.environ.setdefault("UNSTRUCTURED_DISABLE_NOTICE", "1")


def get_partition(parser: str):
    if parser == "docparse":
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from docparse import partition

        def run(path, fmt):
            return [e.to_dict() for e in partition(filename=path)]
        return run
    if parser == "unstructured":
        from unstructured.partition.auto import partition

        def run(path, fmt):
            kw = {"languages": ["eng"]}
            if fmt in ("pdf", "pdf2col"):
                kw.update(strategy="fast")
            elif fmt == "scanned":
                kw.update(strategy="ocr_only")
            return [e.to_dict() for e in partition(filename=path, **kw)]
        return run
    raise SystemExit(f"unknown parser {parser}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parser", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--formats", default="docx,html,md,pdf,pdf2col,scanned")
    a = ap.parse_args()
    run = get_partition(a.parser)
    # warm-up so one-off import/model cost isn't billed to the first document
    first = sorted(glob.glob(os.path.join(a.data, "md", "*.md")))[:1]
    for p in first:
        run(p, "md")
    results = {}
    for fmt in a.formats.split(","):
        for path in sorted(glob.glob(os.path.join(a.data, fmt, "*"))):
            doc = os.path.splitext(os.path.basename(path))[0]
            t0 = time.perf_counter()
            try:
                els, err = run(path, fmt), None
            except Exception as exc:  # a crash is a result too
                els, err = [], f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            results[f"{fmt}/{doc}"] = {"seconds": time.perf_counter() - t0, "elements": els, "error": err}
            print(f"{a.parser:12s} {fmt:8s} {doc} {len(els):4d} els {results[f'{fmt}/{doc}']['seconds']:.2f}s", file=sys.stderr)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(results, f)


if __name__ == "__main__":
    main()
