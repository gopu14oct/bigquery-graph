"""Serialise elements for storage: JSON, JSONL, pandas, and BigQuery."""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from docparse.elements import Element, elements_from_dicts, elements_to_dicts


# ------------------------------------------------------------------ JSON
def elements_to_json(elements: Iterable[Element], filename: Optional[str] = None, indent: int = 4) -> str:
    s = json.dumps(elements_to_dicts(elements), indent=indent, ensure_ascii=False)
    if filename:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(s)
    return s


def elements_from_json(filename: Optional[str] = None, text: Optional[str] = None) -> list[Element]:
    if text is None:
        with open(filename, encoding="utf-8") as f:
            text = f.read()
    return elements_from_dicts(json.loads(text))


def elements_to_jsonl(elements: Iterable[Element], filename: Optional[str] = None) -> str:
    s = "\n".join(json.dumps(d, ensure_ascii=False) for d in elements_to_dicts(elements))
    if filename:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(s + "\n")
    return s


def elements_to_text(elements: Iterable[Element]) -> str:
    return "\n\n".join(e.text for e in elements if e.text)


# ------------------------------------------------------------------ pandas
def elements_to_dataframe(elements: Iterable[Element]):
    import pandas as pd

    rows = []
    for d in elements_to_dicts(elements):
        md = d.pop("metadata")
        md.pop("orig_elements", None)
        rows.append({**d, **md})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ BigQuery
BIGQUERY_SCHEMA: list[dict[str, Any]] = [
    {"name": "element_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "type", "type": "STRING", "mode": "REQUIRED"},
    {"name": "text", "type": "STRING", "mode": "NULLABLE"},
    {"name": "parent_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "category_depth", "type": "INT64", "mode": "NULLABLE"},
    {"name": "sequence", "type": "INT64", "mode": "REQUIRED"},
    {"name": "filename", "type": "STRING", "mode": "NULLABLE"},
    {"name": "filetype", "type": "STRING", "mode": "NULLABLE"},
    {"name": "page_number", "type": "INT64", "mode": "NULLABLE"},
    {"name": "page_name", "type": "STRING", "mode": "NULLABLE"},
    {"name": "text_as_html", "type": "STRING", "mode": "NULLABLE"},
    {"name": "languages", "type": "STRING", "mode": "REPEATED"},
    {"name": "last_modified", "type": "STRING", "mode": "NULLABLE"},
    {"name": "chunk_index", "type": "INT64", "mode": "NULLABLE"},
    {"name": "orig_element_ids", "type": "STRING", "mode": "REPEATED"},
    {"name": "metadata", "type": "JSON", "mode": "NULLABLE"},
]
_PROMOTED = {"parent_id", "category_depth", "filename", "filetype", "page_number", "page_name",
             "text_as_html", "languages", "last_modified", "chunk_index"}


def elements_to_bigquery_rows(elements: Iterable[Element]) -> list[dict[str, Any]]:
    """Flatten elements into rows matching ``BIGQUERY_SCHEMA``.

    Frequently-filtered fields are promoted to columns; the rest of the metadata
    is kept in a ``JSON`` column. ``parent_id`` + ``element_id`` make the document
    hierarchy queryable with a self-join or a BigQuery property graph.
    """
    rows = []
    for seq, el in enumerate(elements):
        md = el.metadata.to_dict()
        orig = md.pop("orig_elements", None)
        row = {
            "element_id": el.id,
            "type": el.category,
            "text": el.text,
            "sequence": seq,
            "languages": md.get("languages") or [],
            "orig_element_ids": [o["element_id"] for o in orig] if orig else [],
        }
        for k in _PROMOTED - {"languages"}:
            row[k] = md.get(k)
        rest = {k: v for k, v in md.items() if k not in _PROMOTED}
        row["metadata"] = json.dumps(rest, ensure_ascii=False) if rest else None
        rows.append(row)
    return rows


def load_to_bigquery(
    elements: Iterable[Element],
    table_id: str,
    write_disposition: str = "WRITE_APPEND",
    client=None,
):  # pragma: no cover - needs GCP credentials
    """Load elements into ``project.dataset.table`` (created if missing). Requires
    ``pip install google-cloud-bigquery`` and application-default credentials."""
    from google.cloud import bigquery

    client = client or bigquery.Client()
    schema = [bigquery.SchemaField(f["name"], f["type"], mode=f["mode"]) for f in BIGQUERY_SCHEMA]
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=write_disposition,
    )
    job = client.load_table_from_json(elements_to_bigquery_rows(elements), table_id, job_config=job_config)
    return job.result()


# ------------------------------------------------------------------ unstructured-compatible names
def convert_to_dict(elements: Iterable[Element]) -> list[dict[str, Any]]:
    return elements_to_dicts(elements)


def dict_to_elements(dicts: Iterable[dict[str, Any]]) -> list[Element]:
    return elements_from_dicts(dicts)


def convert_to_dataframe(elements: Iterable[Element]):
    return elements_to_dataframe(elements)
