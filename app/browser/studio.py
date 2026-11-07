"""Python entry points for the in-browser (Pyodide) build of docparse Studio.

Called from worker.js. Everything crosses the JS boundary as JSON strings.
"""

from __future__ import annotations

import json
import os
import time

from app import exporters
from app.validate import validate
from docparse import chunk, elements_from_dicts, partition
from docparse.filetype import detect_filetype

UPLOADS = "/tmp/uploads"


def parse(filename: str, data: bytes, options_json: str = "{}") -> str:
    opts = json.loads(options_json)
    os.makedirs(UPLOADS, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in os.path.basename(filename)) or "upload"
    path = os.path.join(UPLOADS, f"{int(time.time() * 1000)}-{safe}")
    with open(path, "wb") as f:
        f.write(bytes(data))
    ftype = detect_filetype(filename=path).value
    if ftype in ("unknown", "zip"):
        raise ValueError("This file type is not supported. Try PDF, DOCX, PPTX, XLSX, HTML, Markdown, EML, EPUB, CSV or TXT.")
    is_pdf = ftype == "pdf"
    kwargs: dict = {"metadata_filename": filename}
    if is_pdf:
        kwargs["ocr"] = "never"  # no OCR engine in the browser
        kwargs["infer_table_structure"] = bool(opts.get("tables", True))
    if opts.get("languages"):
        kwargs["languages"] = opts["languages"]
    t0 = time.perf_counter()
    els = [e.to_dict() for e in partition(filename=path, **kwargs)]
    secs = time.perf_counter() - t0
    pages = sizes = text_pages = None
    if is_pdf:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            pages = len(pdf.pages)
            sizes, text_pages = [], []
            for i, p in enumerate(pdf.pages, start=1):
                sizes.append((float(p.width), float(p.height)))
                if len(p.chars) >= 20:
                    text_pages.append(i)
                p.close()
    else:
        nums = [e["metadata"].get("page_number") for e in els if e["metadata"].get("page_number")]
        pages = max(nums) if nums else None
    report = validate(els, "application/pdf" if is_pdf else ftype, None, pages, text_pages)
    if is_pdf and text_pages is not None and pages and len(text_pages) < pages:
        missing = [p for p in range(1, pages + 1) if p not in text_pages]
        report["checks"].insert(0, {"id": "ocr", "label": "Scanned pages", "status": "warn" if len(missing) < pages else "fail",
                                    "detail": f"{len(missing)} of {pages} page(s) are images without a text layer "
                                              f"({', '.join(map(str, missing[:15]))}). This browser version has no OCR, "
                                              "so their content is missing.", "value": missing})
        w = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
        scored = [c for c in report["checks"] if c["status"] in w]
        report["score"] = round(100 * sum(w[c["status"]] for c in scored) / max(1, len(scored)))
    os.remove(path)
    return json.dumps({"elements": els, "validation": report, "page_sizes": sizes, "pages": pages,
                       "seconds": round(secs, 3), "is_pdf": is_pdf, "filetype": ftype})


def make_chunks(elements_json: str, params_json: str) -> str:
    p = json.loads(params_json)
    strategy = p.pop("strategy", "by_title")
    if strategy == "basic":
        p.pop("combine_text_under_n_chars", None)
    p = {k: v for k, v in p.items() if v is not None}
    chunks = chunk(elements_from_dicts(json.loads(elements_json)), strategy, include_orig_elements=False, **p)
    sizes = [len(c.text) for c in chunks]
    return json.dumps({"chunks": [c.to_dict() for c in chunks],
                       "stats": {"count": len(chunks), "avg": round(sum(sizes) / max(1, len(sizes))),
                                 "max": max(sizes, default=0), "min": min(sizes, default=0)}})


def export(elements_json: str, fmt: str, reviews_json: str = "{}", filename: str = "") -> str:
    return exporters.render(json.loads(elements_json), fmt, json.loads(reviews_json), filename)


def extract(elements_json: str, fields_json: str) -> str:
    from app.extract import extract as run

    return json.dumps(run(json.loads(elements_json), json.loads(fields_json)))
