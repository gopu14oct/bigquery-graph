"""Command line: ``python -m docparse FILE [FILE...] [--chunking-strategy by_title] [--format jsonl]``."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from docparse.partition.auto import partition
from docparse.staging import elements_to_bigquery_rows, elements_to_json, elements_to_jsonl, elements_to_text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="docparse", description="Partition documents into structured elements.")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--chunking-strategy", choices=["basic", "by_title"])
    ap.add_argument("--max-characters", type=int, default=500)
    ap.add_argument("--new-after-n-chars", type=int)
    ap.add_argument("--overlap", type=int, default=0)
    ap.add_argument("--combine-text-under-n-chars", type=int)
    ap.add_argument("--include-page-breaks", action="store_true")
    ap.add_argument("--format", choices=["json", "jsonl", "text", "bigquery", "summary"], default="json")
    ap.add_argument("-o", "--output", help="write to this file instead of stdout")
    a = ap.parse_args(argv)

    elements = []
    for f in a.files:
        kwargs = {"include_page_breaks": a.include_page_breaks}
        if a.chunking_strategy:
            kwargs.update(chunking_strategy=a.chunking_strategy, max_characters=a.max_characters,
                          new_after_n_chars=a.new_after_n_chars, overlap=a.overlap)
            if a.chunking_strategy == "by_title":
                kwargs["combine_text_under_n_chars"] = a.combine_text_under_n_chars
        elements.extend(partition(filename=f, **kwargs))

    if a.format == "json":
        out = elements_to_json(elements)
    elif a.format == "jsonl":
        out = elements_to_jsonl(elements)
    elif a.format == "text":
        out = elements_to_text(elements)
    elif a.format == "bigquery":
        out = "\n".join(json.dumps(r, ensure_ascii=False) for r in elements_to_bigquery_rows(elements))
    else:
        counts = Counter(e.category for e in elements)
        out = "\n".join(f"{k:20s} {v}" for k, v in counts.most_common()) + f"\n{'TOTAL':20s} {len(elements)}"
    if a.output:
        with open(a.output, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
    else:
        sys.stdout.write(out + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
