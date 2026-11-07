"""Accuracy on real tagged PDFs: score parser outputs against the authoring tool's tags.

    python benchmarks/score_tagged.py --real /tmp/real [--md out.md]

Uses ``score.score_doc`` (same metrics as the synthetic benchmark). Metrics that the
answer key cannot support are shown as n/a: heading depth/type when the document has
no heading tags, table cells when it has no table tags, furniture (tags mark
furniture as Artifacts but carry no text for it).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score import score_doc  # noqa: E402

METRICS = ["text_sim", "block_recall", "type_acc", "type_acc_lenient", "depth_acc", "parent_acc", "order", "table_cells"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", required=True)
    ap.add_argument("--parsers", nargs="+", default=["docparse", "unstructured"])
    ap.add_argument("--md")
    a = ap.parse_args()
    tdir = os.path.join(a.real, "truth")
    lines = []
    for fn in sorted(os.listdir(tdir)):
        doc = fn.replace(".truth.json", "")
        with open(os.path.join(tdir, fn)) as f:
            truth = json.load(f)
        kinds = {b["type"] for b in truth["blocks"]}
        n = {k: sum(1 for b in truth["blocks"] if b["type"] == k) for k in sorted(kinds)}
        lines.append(f"\n### {doc}  (answer key: {n})\n")
        lines.append("| metric | " + " | ".join(a.parsers) + " |")
        lines.append("|---|" + "---|" * len(a.parsers))
        scores = []
        for p in a.parsers:
            with open(os.path.join(a.real, "results", p, doc + ".json")) as f:
                res = json.load(f)
            scores.append(score_doc(truth, res["elements"]))
        for m in METRICS:
            if m in ("depth_acc",) and "Title" not in kinds:
                vals = ["n/a"] * len(scores)
            elif m == "table_cells" and "Table" not in kinds:
                vals = ["n/a"] * len(scores)
            else:
                nums = [s.get(m) for s in scores]
                best = max(v for v in nums if v is not None)
                vals = [("**%.3f**" % v if v == best and len(set(nums)) > 1 else "%.3f" % v) for v in nums]
            lines.append(f"| {m} | " + " | ".join(vals) + " |")
    text = "\n".join(lines)
    print(text)
    if a.md:
        with open(a.md, "w") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
