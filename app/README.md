# docparse Studio

A mobile-first web app for parsing, inspecting and validating documents with docparse. Open it in your phone's
browser and choose **Add to Home Screen** to install it like an app.

## Features

| | |
|---|---|
| **Upload** | PDF, DOCX, PPTX, XLSX, HTML, Markdown, EML, EPUB, CSV/TSV, XML/JSON, or **scan a page with the camera** (OCR via Tesseract) |
| **Elements** | every parsed element, colour-coded by type, with type filters and full-text search; tap one for its full text and metadata |
| **Pages** | the rendered page with a box drawn over each element; swipe between pages, zoom, show or hide types and labels |
| **Outline** | the heading tree rebuilt from `parent_id` |
| **Tables** | extracted tables rendered as grids; copy any table as CSV |
| **Chunks** | try `by_title` or `basic` chunking live with sliders for size, overlap and section combining, with a size histogram |
| **Validate** | an automatic *parse health* score (text coverage against `pdftotext`, empty pages, fragmentation, over-merged blocks, heading sanity, hierarchy, empty table cells, duplicates) plus **human review**: mark elements correct or wrong, relabel types, add notes, or step through a random sample of 20 in a one-handed review mode. Your accuracy estimate comes with a 95% confidence interval |
| **Compare** | run the open-source `unstructured` library on the same file and see word agreement, type agreement and where the two disagree |
| **Export** | JSON (unstructured-compatible), JSONL, CSV, Markdown, BigQuery rows, and your reviews as an answer key |

Dark and light themes follow the system. The app shell is cached by a service worker.

## Run locally

```bash
pip install -e ".[pdf,app]"            # plus: apt-get install tesseract-ocr poppler-utils
uvicorn app.server:app --host 0.0.0.0 --port 8000
# optional: side-by-side comparison
UNSTRUCTURED_PYTHON=/path/to/venv-with-unstructured/bin/python uvicorn app.server:app --port 8000
```

To use it on your phone over Wi-Fi, open `http://<your-computer-ip>:8000`. The camera, *Add to Home Screen* and
sharing need HTTPS, which a Cloud Run deployment gives you.

## Deploy to Google Cloud Run

```bash
gcloud run deploy docparse-studio --source . --region us-central1 --memory 2Gi --cpu 2 --allow-unauthenticated
```

Uploaded files live on the instance's local disk (`DOCPARSE_APP_DATA`), so they disappear when the instance is
recycled. Mount a Cloud Storage volume at that path to keep them. `--allow-unauthenticated` makes the app public:
put it behind IAP or drop that flag for private use.

## API

| method | path | |
|---|---|---|
| POST | `/api/docs` | multipart `file`, plus optional `ocr` (auto, always or never), `tables`, `languages` |
| GET | `/api/docs`, `/api/docs/{id}` | history; status, elements, validation, reviews |
| GET | `/api/docs/{id}/pages/{n}.png` | rendered page |
| POST | `/api/docs/{id}/chunks` | `{strategy, max_characters, overlap, …}` |
| POST | `/api/docs/{id}/reviews` | `{element_id, verdict, corrected_type, note}` |
| POST | `/api/docs/{id}/compare` | run unstructured (needs `UNSTRUCTURED_PYTHON`) |
| GET | `/api/docs/{id}/export?fmt=` | `json`, `jsonl`, `csv`, `markdown`, `bigquery` or `reviews` |

## Browser-only version (no server)

`app/browser/` is the same app with docparse running **inside the browser**: Python via Pyodide in a Web Worker,
and pdf.js for page images. Documents never leave the device and are stored in IndexedDB.

```bash
python app/browser/build.py --out dist/    # downloads Pyodide, pdf.js and pure-Python wheels, bundles docparse
```

Serve `dist/` from any static host, or publish it as a claude.ai artifact. The first load downloads about 24 MB,
which the browser then caches.

Compared with the server version, it lacks:
- **OCR:** no Tesseract, so scanned pages and photos produce no text.
- **The Unstructured comparison.**
- **Encrypted PDFs:** AES-encrypted PDFs are not supported, because the native `cryptography` package is replaced by a stub.

Exports are copy-to-clipboard, because the artifact sandbox blocks downloads.

The artifact host serves binary archives (`.zip`, `.whl`) with HTTP 415. The build therefore stores them
base64-encoded as `.txt`, and the worker decodes them in memory.
