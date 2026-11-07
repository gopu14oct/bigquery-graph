# Benchmark: docparse vs. unstructured (open source)

## TL;DR (held-out test set, 20 documents × 6 formats)

| metric (higher is better unless noted) | docparse | unstructured 0.27.37 |
|---|---|---|
| text similarity | **0.999** | 0.978 |
| block recall (each paragraph, heading or list item recovered as exactly one element) | **0.993** | 0.849 |
| element type accuracy | **1.000** | 0.947 |
| heading depth accuracy | **0.919** | 0.718 |
| parent_id (hierarchy) accuracy | **0.967** | 0.650 |
| reading order | 1.000 | 1.000 |
| table cell accuracy | **0.971** | 0.500 |
| header/footer leakage into body (lower is better) | **0.000** | 0.167 |
| seconds per document (lower is better) | **0.99** | 2.40 |

Per-format tables are in [`RESULTS_test.md`](RESULTS_test.md).

- **DOCX, HTML and Markdown:** both parsers are essentially perfect. docparse is 6–30× faster, and on Markdown its section parents are right where unstructured's are not (1.00 vs 0.93).
- **Born-digital PDF (LibreOffice):** unstructured `fast` recovers only 60% of blocks as single elements, because it merges headings into the paragraph below them. Its `parent_id` is almost always wrong (0.008), and it extracts no tables.
- **Two-column PDF:** unstructured's reading order is also correct. It gets heading depth almost always wrong (0.14) and extracts no tables.
- **Scanned PDF:** unstructured `ocr_only` leaks the running header and footer into the text on every document and extracts no tables. docparse's OCR heading depth (0.52) is its weakest score, but still better than unstructured's 0.19.

## Read this before quoting the numbers

1. **The corpus is synthetic and I wrote both the generator and docparse.** I kept a *dev* split (seeds 1000+) for tuning and generated the *test* split (seeds 9000+) only once, after all tuning was done. That protects against overfitting to particular documents, but **not against overfitting to the generator's style.** Real-world documents have things this corpus lacks: borderless tables, sidebars, figures with captions, footnotes, forms, rotated pages, low-quality scans, non-English text and math. Expect smaller margins or losses on those. Run `docparse` on your own documents before relying on it.
2. **unstructured `hi_res` was not tested.** It downloads layout models (YOLOX / Detectron2) from huggingface.co, which this sandbox's network blocks. `hi_res` is unstructured's strongest PDF strategy and is designed to fix exactly the PDF weaknesses above (tables, titles). A fair comparison against `hi_res` still needs to be done.
3. **unstructured's paid API and platform were not tested.**
4. Unstructured was run with the best strategy available offline: `fast` for born-digital PDFs and `ocr_only` for scans. That is what `strategy="auto"` picks when `hi_res` is unavailable. Everything else ran with defaults and `languages=["eng"]`.
5. Timings come from a shared cloud container (single run, after a warm-up). Treat them as relative, not absolute.

## Method

`generate.py` samples a document **spec** with exact ground truth:
- a title, then 3–5 sections;
- level-2/3 headings, paragraphs of real English sentences (taken from Python stdlib docstrings), bullet lists with nesting, and ruled tables;
- a running header and footer.

Each spec is rendered six ways:

| format | how |
|---|---|
| `docx` | python-docx with real Heading / List Bullet styles |
| `html` | semantic HTML with `<header>` and `<footer>` |
| `md` | CommonMark + GFM tables |
| `pdf` | the DOCX rendered by LibreOffice (real embedded fonts) |
| `pdf2col` | reportlab, two-column layout, ruled tables |
| `scanned` | the LibreOffice PDF rasterised at 150 dpi (image only) |

`score.py` matches ground-truth blocks to predicted elements one-to-one (fuzzy ratio ≥ 90) and computes the metrics. Their exact definitions are in the module docstring.

## Reproduce

```bash
python benchmarks/generate.py --out /tmp/bench --split dev  --n 12
python benchmarks/generate.py --out /tmp/bench --split test --n 20
python benchmarks/run_parser.py --parser docparse --data /tmp/bench/test --out /tmp/bench/res/docparse.json
python -m venv /tmp/uvenv && /tmp/uvenv/bin/pip install "unstructured[pdf,docx,pptx,md]"
/tmp/uvenv/bin/python benchmarks/run_parser.py --parser unstructured --data /tmp/bench/test --out /tmp/bench/res/unstructured.json
python benchmarks/score.py --data /tmp/bench/test /tmp/bench/res/docparse.json /tmp/bench/res/unstructured.json
```

You need LibreOffice (`soffice`), poppler (`pdftoppm`), `tesseract-ocr`, `pip install rapidfuzz` and the docparse test extras.
Versions used: unstructured 0.27.37, unstructured-inference 1.6.17, pdfminer.six 20260107, pdfplumber 0.11.10, tesseract 5.3.4.

## What changed in docparse during the dev phase (all general fixes, none document-specific)

- PDF line building: words are grouped into vertical bands, then split at gaps wider than ~1.2 em. This stops text crossing column gutters and copes with OCR's out-of-order word boxes.
- XY-cut prefers a clear vertical gutter over a thin horizontal gap, so the left column is read before the right one.
- Paragraph splitting: the document's typical line gap is learned, list hanging indents are respected, and a "deliberate line break" test asks whether the next line's first word would have fitted on the previous line.
- Paragraphs that continue across a column or page break are re-joined.
- Tables take part in the reading-order (XY-cut) pass instead of being inserted by y-position.
- List depth is ranked among the item indents in the same column.
- Small margin text is treated as header/footer even in single-page documents.
- New OCR path (`partition/ocr.py`): Tesseract words; font size from glyph heights; emphasis from stroke width and colour; bullets misread by OCR are repaired; ruled tables are detected from image pixel runs.
- Heading levels come from clustering the sizes of heading candidates (wider tolerance for OCR).
- DOCX: list level is read from the style definition ("List Bullet 2").
