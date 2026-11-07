"""docparse Studio - a mobile-first web app to parse, inspect and validate documents.

    pip install -e ".[app]"            # fastapi, uvicorn, python-multipart, pypdfium2
    uvicorn app.server:app --host 0.0.0.0 --port 8000

Environment:
    DOCPARSE_APP_DATA      where uploads/results are kept (default ./app_data)
    UNSTRUCTURED_PYTHON    python interpreter that has `unstructured` installed; enables
                           the side-by-side comparison (optional)
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import exporters
from app.validate import validate
from docparse import chunk, elements_from_dicts, partition
from docparse.filetype import detect_filetype

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.environ.get("DOCPARSE_APP_DATA", os.path.join(os.getcwd(), "app_data")))
UNSTRUCTURED_PY = os.environ.get("UNSTRUCTURED_PYTHON")
MAX_UPLOAD = int(os.environ.get("DOCPARSE_MAX_UPLOAD_MB", "100")) * 1024 * 1024
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif", ".heic"}
os.makedirs(DATA, exist_ok=True)

app = FastAPI(title="docparse Studio")
pool = ThreadPoolExecutor(max_workers=int(os.environ.get("DOCPARSE_WORKERS", "2")))
lock = threading.Lock()


# ------------------------------------------------------------------ storage
def ddir(doc_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{12}", doc_id):
        raise HTTPException(404, "unknown document")
    d = os.path.join(DATA, doc_id)
    if not os.path.isdir(d):
        raise HTTPException(404, "unknown document")
    return d


def read_json(path: str, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def meta(doc_id: str) -> dict:
    return read_json(os.path.join(ddir(doc_id), "meta.json"), {})


def set_meta(doc_id: str, **kw) -> dict:
    with lock:
        m = meta(doc_id)
        m.update(kw)
        write_json(os.path.join(ddir(doc_id), "meta.json"), m)
        return m


# ------------------------------------------------------------------ parsing
def image_to_pdf(src: str, dst: str) -> None:
    from PIL import Image, ImageOps

    try:
        from pillow_heif import register_heif_opener  # optional, for iPhone photos
        register_heif_opener()
    except ImportError:
        pass
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        # OCR wants ~300 dpi text; phone photos are large, so cap the long side
        scale = min(1.0, 3500 / max(im.size))
        if scale < 1:
            im = im.resize((int(im.width * scale), int(im.height * scale)))
        im.save(dst, "PDF", resolution=200)


def page_info(pdf_path: str) -> tuple[int, list[int], list[tuple[float, float]]]:
    """(page count, pages that have a text layer, page sizes in points)."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
        text_pages, sizes = [], []
        for i, p in enumerate(pdf.pages, start=1):
            sizes.append((float(p.width), float(p.height)))
            if len(p.chars) >= 20:
                text_pages.append(i)
            p.close()
    return n, text_pages, sizes


def run_parse(doc_id: str, options: dict) -> None:
    d = os.path.join(DATA, doc_id)
    m = read_json(os.path.join(d, "meta.json"))
    try:
        set_meta(doc_id, status="parsing", progress="Reading document…")
        path = os.path.join(d, m["stored"])
        kwargs: dict = {"metadata_filename": m["filename"]}
        if m["is_pdf"]:
            kwargs["ocr"] = options.get("ocr", "auto")
            kwargs["infer_table_structure"] = options.get("tables", True)
        if options.get("languages"):
            kwargs["languages"] = options["languages"]
        t0 = time.perf_counter()
        els = partition(filename=path, **kwargs)
        secs = time.perf_counter() - t0
        dicts = [e.to_dict() for e in els]
        page_count = text_pages = sizes = None
        if m["is_pdf"]:
            set_meta(doc_id, progress="Validating…")
            page_count, text_pages, sizes = page_info(path)
        else:
            pages = [e["metadata"].get("page_number") for e in dicts if e["metadata"].get("page_number")]
            page_count = max(pages) if pages else None
        report = validate(dicts, "application/pdf" if m["is_pdf"] else m.get("filetype", ""), path if m["is_pdf"] else None,
                          page_count, text_pages)
        write_json(os.path.join(d, "result.json"), {"elements": dicts, "validation": report, "page_sizes": sizes})
        set_meta(doc_id, status="done", progress="", seconds=round(secs, 3), pages=page_count,
                 elements=len(dicts), score=report["score"], type_counts=report["type_counts"])
    except Exception as exc:  # noqa: BLE001
        set_meta(doc_id, status="error", progress="", error=f"{type(exc).__name__}: {exc}",
                 trace=traceback.format_exc()[-2000:])


# ------------------------------------------------------------------ API
@app.post("/api/docs")
async def upload(file: UploadFile = File(...), ocr: str = Form("auto"), tables: bool = Form(True),
                 languages: str = Form("")):
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD // 1024 // 1024} MB)")
    if not data:
        raise HTTPException(400, "Empty file")
    doc_id = uuid.uuid4().hex[:12]
    d = os.path.join(DATA, doc_id)
    os.makedirs(d)
    name = os.path.basename(file.filename or "upload")
    ext = os.path.splitext(name)[1].lower()
    original = "original" + ext
    with open(os.path.join(d, original), "wb") as f:
        f.write(data)
    is_image = ext in IMAGE_EXT or (file.content_type or "").startswith("image/")
    stored = original
    if is_image:
        stored = "image.pdf"
        image_to_pdf(os.path.join(d, original), os.path.join(d, stored))
        ocr = "always"
    ftype = detect_filetype(filename=os.path.join(d, stored)).value
    if ftype == "unknown":
        shutil.rmtree(d)
        raise HTTPException(415, "Unsupported file type")
    m = {"id": doc_id, "filename": name, "stored": stored, "original": original, "size": len(data),
         "kind": "image" if is_image else ftype, "is_pdf": ftype == "pdf", "filetype": ftype,
         "uploaded": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
         "status": "queued", "progress": "Queued…", "options": {"ocr": ocr, "tables": tables}}
    write_json(os.path.join(d, "meta.json"), m)
    langs = [x.strip() for x in languages.split(",") if x.strip()]
    pool.submit(run_parse, doc_id, {"ocr": ocr, "tables": tables, "languages": langs})
    return m


@app.get("/api/docs")
def list_docs():
    out = []
    for doc_id in os.listdir(DATA):
        m = read_json(os.path.join(DATA, doc_id, "meta.json"))
        if m:
            m.pop("trace", None)
            out.append(m)
    return sorted(out, key=lambda m: m.get("uploaded", ""), reverse=True)


@app.get("/api/docs/{doc_id}")
def get_doc(doc_id: str):
    m = meta(doc_id)
    res = read_json(os.path.join(ddir(doc_id), "result.json")) if m.get("status") == "done" else None
    reviews = read_json(os.path.join(ddir(doc_id), "reviews.json"), {})
    return {"meta": m, "result": res, "reviews": reviews}


@app.delete("/api/docs/{doc_id}")
def delete_doc(doc_id: str):
    shutil.rmtree(ddir(doc_id))
    return {"ok": True}


@app.post("/api/docs/{doc_id}/reparse")
def reparse(doc_id: str, ocr: str = Form("auto"), tables: bool = Form(True)):
    m = set_meta(doc_id, status="queued", progress="Queued…", options={"ocr": ocr, "tables": tables})
    pool.submit(run_parse, doc_id, {"ocr": ocr, "tables": tables})
    return m


@app.get("/api/docs/{doc_id}/file")
def original_file(doc_id: str):
    m = meta(doc_id)
    return FileResponse(os.path.join(ddir(doc_id), m["original"]), filename=m["filename"])


@app.get("/api/docs/{doc_id}/pages/{page}.png")
def page_png(doc_id: str, page: int, scale: float = 1.5):
    m = meta(doc_id)
    if not m.get("is_pdf"):
        raise HTTPException(404, "page images are only available for PDFs and photos")
    d = ddir(doc_id)
    scale = max(0.5, min(scale, 3.0))
    cache = os.path.join(d, f"page-{page}-{scale:.2f}.png")
    if not os.path.exists(cache):
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(os.path.join(d, m["stored"]))
        if not 1 <= page <= len(pdf):
            raise HTTPException(404, "no such page")
        img = pdf[page - 1].render(scale=scale).to_pil()
        img.save(cache, optimize=True)
        pdf.close()
    return FileResponse(cache, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


class Review(BaseModel):
    element_id: str
    verdict: Optional[str] = None          # "ok" | "wrong" | None (clear)
    corrected_type: Optional[str] = None
    corrected_text: Optional[str] = None
    note: Optional[str] = None


@app.post("/api/docs/{doc_id}/reviews")
def add_review(doc_id: str, r: Review):
    path = os.path.join(ddir(doc_id), "reviews.json")
    with lock:
        reviews = read_json(path, {})
        if r.verdict is None and not r.corrected_type and not r.note and r.corrected_text is None:
            reviews.pop(r.element_id, None)
        else:
            reviews[r.element_id] = {k: v for k, v in r.model_dump().items() if v not in (None, "")} | {
                "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        write_json(path, reviews)
    return reviews


class ChunkReq(BaseModel):
    strategy: str = "by_title"
    max_characters: int = 1000
    new_after_n_chars: Optional[int] = None
    overlap: int = 0
    overlap_all: bool = False
    combine_text_under_n_chars: Optional[int] = None


@app.post("/api/docs/{doc_id}/chunks")
def make_chunks(doc_id: str, req: ChunkReq):
    res = read_json(os.path.join(ddir(doc_id), "result.json"))
    if not res:
        raise HTTPException(409, "document not parsed yet")
    kw = req.model_dump(exclude={"strategy"}, exclude_none=True)
    if req.strategy == "basic":
        kw.pop("combine_text_under_n_chars", None)
    try:
        chunks = chunk(elements_from_dicts(res["elements"]), req.strategy, include_orig_elements=False, **kw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    sizes = [len(c.text) for c in chunks]
    return {"chunks": [c.to_dict() for c in chunks],
            "stats": {"count": len(chunks), "avg": round(sum(sizes) / max(1, len(sizes))), "max": max(sizes, default=0),
                      "min": min(sizes, default=0)}}


class ExtractReq(BaseModel):
    fields: list[dict]


@app.post("/api/docs/{doc_id}/extract")
def extract_fields(doc_id: str, req: ExtractReq):
    """Quick (rule-based) field extraction from the parsed elements."""
    from app.extract import extract

    res = read_json(os.path.join(ddir(doc_id), "result.json"))
    if not res:
        raise HTTPException(409, "document not parsed yet")
    return {"results": extract(res["elements"], req.fields)}


@app.get("/api/capabilities")
def capabilities():
    return {"compare": bool(UNSTRUCTURED_PY and os.path.exists(UNSTRUCTURED_PY)),
            "ocr": shutil.which("tesseract") is not None}


COMPARE_WORKER = r'''
import json, os, sys, time
os.environ["UNSTRUCTURED_DISABLE_NOTICE"] = "1"
from unstructured.partition.auto import partition
path, is_pdf, ocr = sys.argv[1], sys.argv[2] == "1", sys.argv[3]
kw = {"languages": ["eng"]}
if is_pdf:
    kw["strategy"] = "ocr_only" if ocr == "always" else "fast"
t0 = time.perf_counter()
els = partition(filename=path, **kw)
print(json.dumps({"seconds": time.perf_counter() - t0, "elements": [e.to_dict() for e in els]}))
'''


@app.post("/api/docs/{doc_id}/compare")
def compare(doc_id: str):
    if not (UNSTRUCTURED_PY and os.path.exists(UNSTRUCTURED_PY)):
        raise HTTPException(501, "Comparison is not configured on this server (set UNSTRUCTURED_PYTHON).")
    d = ddir(doc_id)
    cached = read_json(os.path.join(d, "compare.json"))
    if cached:
        return cached
    m = meta(doc_id)
    res = read_json(os.path.join(d, "result.json"))
    if not res:
        raise HTTPException(409, "document not parsed yet")
    try:
        p = subprocess.run([UNSTRUCTURED_PY, "-c", COMPARE_WORKER, os.path.join(d, m["stored"]), "1" if m["is_pdf"] else "0",
                            m.get("options", {}).get("ocr", "auto")], capture_output=True, text=True, timeout=600,
                           env={**os.environ, "DO_NOT_TRACK": "true", "SCARF_NO_ANALYTICS": "true"})
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "unstructured took longer than 10 minutes") from exc
    if p.returncode != 0:
        raise HTTPException(500, "unstructured failed: " + (p.stderr.strip().splitlines() or ["?"])[-1][:300])
    other = json.loads(p.stdout.strip().splitlines()[-1])
    out = {"unstructured": other, "summary": compare_summary(res["elements"], other["elements"]),
           "docparse_seconds": m.get("seconds")}
    write_json(os.path.join(d, "compare.json"), out)
    return out


def compare_summary(a: list[dict], b: list[dict]) -> dict:
    from rapidfuzz import fuzz

    def norm(t):
        return " ".join(t.split()).lower()
    wa = collections.Counter(w for e in a for w in re.findall(r"\w+", e["text"].lower()))
    wb = collections.Counter(w for e in b for w in re.findall(r"\w+", e["text"].lower()))
    inter = sum((wa & wb).values())
    agree = 2 * inter / max(1, sum(wa.values()) + sum(wb.values()))
    # align elements: same text (fuzzy >= 90) => compare types
    bt = [(norm(e["text"]), e["type"]) for e in b if e["text"].strip()]
    used = set()
    same_type = diff_type = only_a = 0
    diffs = []
    for e in a:
        ta = norm(e["text"])
        if not ta:
            continue
        best, bj = 0, None
        for j, (tb, _) in enumerate(bt):
            if j in used or abs(len(tb) - len(ta)) > 0.3 * len(ta) + 5:
                continue
            r = fuzz.ratio(ta, tb)
            if r > best:
                best, bj = r, j
                if r == 100:
                    break
        if best >= 90:
            used.add(bj)
            if bt[bj][1] == e["type"]:
                same_type += 1
            else:
                diff_type += 1
                if len(diffs) < 50:
                    diffs.append({"text": e["text"][:200], "docparse": e["type"], "unstructured": bt[bj][1]})
        else:
            only_a += 1
    return {"word_agreement": round(agree, 4), "aligned_same_type": same_type, "aligned_different_type": diff_type,
            "only_docparse": only_a, "only_unstructured": len(bt) - len(used),
            "types_docparse": dict(collections.Counter(e["type"] for e in a).most_common()),
            "types_unstructured": dict(collections.Counter(e["type"] for e in b).most_common()),
            "type_disagreements": diffs}


@app.get("/api/docs/{doc_id}/export")
def export(doc_id: str, fmt: str = "json"):
    m = meta(doc_id)
    res = read_json(os.path.join(ddir(doc_id), "result.json"))
    if not res:
        raise HTTPException(409, "document not parsed yet")
    if fmt not in exporters.FORMATS:
        raise HTTPException(400, "unknown format")
    reviews = read_json(os.path.join(ddir(doc_id), "reviews.json"), {})
    body = exporters.render(res["elements"], fmt, reviews, m["filename"])
    mt, ext = exporters.FORMATS[fmt]
    base = os.path.splitext(m["filename"])[0]
    return Response(body, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{base}.{ext}"'})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.exception_handler(Exception)
async def unhandled(_req, exc):  # pragma: no cover
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")
