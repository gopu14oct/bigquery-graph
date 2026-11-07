"""Export formats shared by the server and the in-browser (Pyodide) build of docparse Studio."""

from __future__ import annotations

import csv
import io
import json
import re

from docparse import elements_from_dicts, elements_to_bigquery_rows

# format -> (media type, file extension)
FORMATS = {
    "json": ("application/json", "json"),
    "jsonl": ("application/x-ndjson", "jsonl"),
    "bigquery": ("application/x-ndjson", "bq.jsonl"),
    "csv": ("text/csv", "csv"),
    "markdown": ("text/markdown", "md"),
    "reviews": ("application/json", "reviews.json"),
}


def render(els: list[dict], fmt: str, reviews: dict | None = None, filename: str = "") -> str:
    if fmt == "json":
        return json.dumps(els, indent=2, ensure_ascii=False)
    if fmt == "jsonl":
        return "\n".join(json.dumps(e, ensure_ascii=False) for e in els) + "\n"
    if fmt == "bigquery":
        rows = elements_to_bigquery_rows(elements_from_dicts(els))
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["element_id", "type", "page", "depth", "parent_id", "text"])
        for e in els:
            md = e["metadata"]
            w.writerow([e["element_id"], e["type"], md.get("page_number", ""), md.get("category_depth", ""),
                        md.get("parent_id", ""), e["text"]])
        return buf.getvalue()
    if fmt == "markdown":
        return to_markdown(els)
    if fmt == "reviews":
        reviews = reviews or {}
        rows = []
        for e in els:
            r = reviews.get(e["element_id"])
            if r:
                rows.append({"element_id": e["element_id"], "page": e["metadata"].get("page_number"),
                             "predicted_type": e["type"], "predicted_text": e["text"], **r})
        return json.dumps({"document": filename, "reviews": rows}, indent=2, ensure_ascii=False)
    raise ValueError(f"unknown format {fmt!r}")


def to_markdown(els: list[dict]) -> str:
    out = []
    for e in els:
        t, md = e["type"], e["metadata"]
        if t in ("Header", "Footer", "PageBreak"):
            continue
        if t == "Title":
            out.append("#" * min(6, (md.get("category_depth") or 0) + 1) + " " + e["text"])
        elif t == "ListItem":
            out.append("  " * (md.get("category_depth") or 0) + "- " + e["text"])
        elif t in ("Table", "TableChunk") and md.get("text_as_html"):
            rows = re.findall(r"<tr>(.*?)</tr>", md["text_as_html"])
            grid = [[c.replace("|", "\\|") for c in re.findall(r"<td>(.*?)</td>", r)] for r in rows]
            if grid:
                out.append("| " + " | ".join(grid[0]) + " |")
                out.append("|" + "---|" * len(grid[0]))
                out += ["| " + " | ".join(r) + " |" for r in grid[1:]]
        elif t == "CodeSnippet":
            out.append("```\n" + e["text"] + "\n```")
        elif t == "Image":
            out.append("*[image]*")
        elif t == "FigureCaption":
            out.append(f"*{e['text']}*")
        else:
            out.append(e["text"])
        out.append("")
    return "\n".join(out)
