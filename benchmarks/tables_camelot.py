"""Table accuracy on real PDFs, using camelot's test suite as the answer key.

camelot (https://github.com/camelot-dev/camelot) ships real government/statistics
PDFs together with the exact expected cell contents of their tables. We use only the
cases camelot itself extracts with *default* settings (no hand-given table areas or
column positions), since neither parser under test gets such hints.

    python benchmarks/tables_camelot.py build  --camelot /home/user/camelot-dev/camelot --out /tmp/tables
    python benchmarks/realworld.py run --out /tmp/tables --parser docparse
    VENV/python benchmarks/realworld.py run --out /tmp/tables --parser unstructured --python VENV/python
    python benchmarks/tables_camelot.py score --out /tmp/tables

Score per expected table: the best-matching predicted table (Table/TableChunk with
text_as_html), cells compared after whitespace normalisation, allowing the best
row/column offset (-3..3) so a parser that keeps or drops a title row is not punished
twice. ``found`` = share of expected tables matched with any cell overlap.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score import html_rows, norm  # noqa: E402

# (name, file under tests/files, page, kind, [data variable names in tests/data.py])
CASES = [
    ("us030-lattice", "tabula/icdar2013-dataset/competition-dataset-us/us-030.pdf", 2, "ruled", ["data_lattice"]),
    ("twotables-lattice", "twotables_2.pdf", None, "ruled", ["data_lattice_two_tables_1", "data_lattice_two_tables_2"]),
    ("rotated-cw-lattice", "clockwise_table_1.pdf", None, "ruled, rotated 90", ["data_lattice_table_rotated"]),
    ("rotated-acw-lattice", "anticlockwise_table_1.pdf", None, "ruled, rotated 90", ["data_lattice_table_rotated"]),
    ("arabic-lattice", "tabula/arabic.pdf", None, "ruled, Arabic", ["data_arabic"]),
    ("health-stream", "health.pdf", None, "borderless", ["data_stream"]),
    ("rotated-cw-stream", "clockwise_table_2.pdf", None, "borderless, rotated 90", ["data_stream_table_rotated"]),
    ("rotated-acw-stream", "anticlockwise_table_2.pdf", None, "borderless, rotated 90", ["data_stream_table_rotated"]),
    ("census-two-stream", "tabula/12s0324.pdf", None, "borderless, two tables", ["data_stream_two_tables_1", "data_stream_two_tables_2"]),
]


def load_data_literals(path: str) -> dict:
    """Read camelot's expected tables *without executing* its file: only literal list
    assignments and simple ``name[a:b]`` aliases are evaluated (via ``ast``)."""
    with open(path) as f:
        tree = ast.parse(f.read())
    env: dict = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        name, val = node.targets[0].id, node.value
        try:
            env[name] = ast.literal_eval(val)
        except ValueError:
            if isinstance(val, ast.Subscript) and isinstance(val.value, ast.Name) and isinstance(val.slice, ast.Slice) \
                    and val.value.id in env:
                lo = ast.literal_eval(val.slice.lower) if val.slice.lower else None
                hi = ast.literal_eval(val.slice.upper) if val.slice.upper else None
                env[name] = env[val.value.id][lo:hi]
            elif isinstance(val, ast.Call) and isinstance(val.func, ast.Name) and val.func.id == "list" \
                    and val.args and isinstance(val.args[0], ast.Name) and val.args[0].id in env:
                env[name] = list(env[val.args[0].id])
    return env


def build(camelot: str, out: str) -> None:
    data = load_data_literals(os.path.join(camelot, "tests", "data.py"))
    os.makedirs(os.path.join(out, "corpus"), exist_ok=True)
    os.makedirs(os.path.join(out, "truth"), exist_ok=True)
    manifest = {}
    for name, fn, _page, kind, vars_ in CASES:
        src = os.path.join(camelot, "tests", "files", fn)
        shutil.copy(src, os.path.join(out, "corpus", name + ".pdf"))
        tables = [[[norm(str(c)) for c in row] for row in data[v]] for v in vars_]
        with open(os.path.join(out, "truth", name + ".tables.json"), "w") as f:
            json.dump({"kind": kind, "tables": tables}, f)
        with open(os.path.join(out, "corpus", name + ".ref.txt"), "w") as f:
            f.write("")
        import subprocess
        info = subprocess.run(["pdfinfo", os.path.join(out, "corpus", name + ".pdf")], capture_output=True, text=True).stdout
        pages = int(next((ln.split()[-1] for ln in info.splitlines() if ln.startswith("Pages:")), "1"))
        manifest[name] = {"kind": "digital", "desc": kind, "pages": pages}
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(json.dumps(manifest, indent=1))


def cell_score(expected: list[list[str]], pred: list[list[str]]) -> float:
    total = sum(1 for r in expected for c in r if c)
    if not total:
        return 0.0
    best = 0
    for dr in range(-3, 4):
        for dc in range(-3, 4):
            hit = 0
            for r, row in enumerate(expected):
                for c, v in enumerate(row):
                    if not v:
                        continue
                    pr, pc = r + dr, c + dc
                    if 0 <= pr < len(pred) and 0 <= pc < len(pred[pr]) and norm(pred[pr][pc]) == v:
                        hit += 1
            best = max(best, hit)
    return best / total


def score(out: str, parsers: list[str], md: str | None) -> None:
    lines = ["| case | kind | expected tables | " + " | ".join(f"{p} cells" for p in parsers) + " | " +
             " | ".join(f"{p} tables found" for p in parsers) + " |",
             "|---|---|---|" + "---|" * (2 * len(parsers))]
    agg = {p: [] for p in parsers}
    for name, *_ in CASES:
        with open(os.path.join(out, "truth", name + ".tables.json")) as f:
            truth = json.load(f)
        cells, found = [], []
        for p in parsers:
            with open(os.path.join(out, "results", p, name + ".json")) as f:
                res = json.load(f)
            preds = [html_rows(e["metadata"].get("text_as_html", "")) for e in res["elements"]
                     if e["type"] in ("Table", "TableChunk") and e["metadata"].get("text_as_html")]
            scores = []
            for exp in truth["tables"]:
                scores.append(max((cell_score(exp, pr) for pr in preds), default=0.0))
            agg[p].extend(scores)
            cells.append(sum(scores) / len(scores))
            found.append(f"{len(preds)} pred / {sum(1 for s in scores if s > 0)} matched")
        lines.append(f"| {name} | {truth['kind']} | {len(truth['tables'])} | " + " | ".join(f"{c:.3f}" for c in cells) +
                     " | " + " | ".join(found) + " |")
    lines.append("")
    lines.append("| parser | mean cell accuracy over all expected tables |")
    lines.append("|---|---|")
    for p in parsers:
        lines.append(f"| {p} | {sum(agg[p]) / max(1, len(agg[p])):.3f} |")
    text = "\n".join(lines)
    print(text)
    if md:
        with open(md, "w") as f:
            f.write(text + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "score"])
    ap.add_argument("--camelot", default="/home/user/camelot-dev/camelot")
    ap.add_argument("--out", required=True)
    ap.add_argument("--md")
    a = ap.parse_args()
    if a.cmd == "build":
        build(a.camelot, a.out)
    else:
        score(a.out, ["docparse", "unstructured"], a.md)


if __name__ == "__main__":
    main()
