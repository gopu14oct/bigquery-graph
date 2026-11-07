# Held-out evaluation protocol (pre-registered)

This file was written and committed **before** either parser was run on any of the documents below.
Its purpose is to stop the evaluation being tuned to its own results.

## Frozen code

The parsers are fixed at docparse commit `4e39211` ("Real-world PDF accuracy evaluation and fixes it exposed") and
unstructured 0.27.37. **No parser code may change during this evaluation.** If a bug is found, it is reported, not
fixed, and the numbers are left as they are.

## Corpora (selected by rule, not by inspection)

None of these documents was used during development. The one ICDAR file used earlier (us-030) is excluded.

| id | source | selection rule | answer key |
|---|---|---|---|
| **H1 robustness** | Mozilla pdf.js test corpus (`test/pdfs/*.pdf`, 1,089 real-world bug-report PDFs) | `random.Random(2026).sample(sorted(files), 300)` | `pdftotext` (poppler) |
| **H2 structure** | same pdf.js corpus | every file that is tagged, has ≥ 2 pages, and gives ≥ 20 text blocks from `tagged_truth.build` (unchanged); files whose answer key cannot be built are skipped and listed | the PDF's own accessibility tags |
| **H3 tables** | ICDAR 2013 Table Competition (EU + US government PDFs, shipped in camelot's tests) | all 67 documents with `-str.xml`, minus `us-030` | the official `-str.xml` cell structure |

## Parser settings (fixed)

- docparse: `partition(filename)`, defaults (OCR is used automatically only for image-only pages).
- unstructured: `partition_pdf(filename, strategy="fast")`. `hi_res` cannot run in this sandbox because its model download is blocked. For scanned files (no text layer), `strategy="ocr_only"`.
- Each document runs in its own process with a **120 s timeout**.

## Metrics (fixed in advance)

- **H1:** crash rate, timeout rate, and *silent failure* rate (parser returns < 10% of the words `pdftotext` finds, when `pdftotext` finds ≥ 50 words); word F1 against `pdftotext`; seconds per page.
- **H2:** `score.score_doc` unchanged (text_sim, block_recall, type_acc, type_acc_lenient, depth_acc, parent_acc, order). Micro-averaged over documents by number of truth blocks, and macro-averaged per document.
- **H3:** for each ground-truth table, the best predicted table on the same page:
  - (a) **adjacency-relation F1**, the ICDAR 2013 structure metric: for every non-empty cell, its nearest non-empty neighbour to the right and below, compared as (text, text, direction) triples;
  - (b) the cell-grid score already used in `tables_camelot.py` (exact cell text with the best row/column offset).
  
  Both are averaged over all ground-truth tables, with missed tables counted as 0. Table detection recall is reported too.

## Reporting rules

- All numbers are reported, including those where docparse loses.
- 95% bootstrap confidence intervals (1,000 resamples over documents, seed 7) for the headline metrics.
- Any document a parser crashes or times out on scores 0 for that parser. It is not dropped.

## Amendment 1 (before any parser was run)

Written after corpus selection and before either parser had run on any held-out file.

1. **H2 had no eligible document in pdf.js.** All 30 tagged multi-page pdf.js files are fuzzed or deliberately
   broken, so no answer key could be built. Under the same rule (tagged, ≥ 2 pages, ≥ 20 tag blocks), the source
   is widened to every PDF in these third-party test corpora:
   - pdfplumber `tests/pdfs`
   - PyMuPDF `tests`
   - Apache PDFBox `pdfbox/src/test/resources`
   - the pdfminer.six, borb, tabula-py, pypdf, pikepdf and ocrmypdf sdists
   - py-pdf/sample-files
   - unstructured `example-docs`

   Any file whose SHA-256 matches a document used earlier is excluded. Result: **14 documents**, listed in
   `h2/manifest.json`.
2. **"No text layer"** (used to choose unstructured's `ocr_only`) means `pdffonts` lists no fonts. It no longer
   means "pdftotext finds < 20 words": pdf.js contains many tiny test files that have a text layer but almost no
   words, and calling those scans would be wrong.
