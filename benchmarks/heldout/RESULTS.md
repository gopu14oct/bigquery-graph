# Held-out results (pre-registered, frozen code)

The protocol is in [PROTOCOL.md](PROTOCOL.md), committed before any run. docparse was frozen at `4e39211` and
`git diff 4e39211 -- docparse` was empty when these numbers were taken. unstructured 0.27.37 ran with
`fast`/`ocr_only` (`hi_res` cannot run in this sandbox). None of these documents was used during development.

## Summary

| | verdict | evidence |
|---|---|---|
| **Robustness** (300 random real-world PDFs) | **tie** | 5 vs 5 crashes. 0 vs 1 timeout. Silent failures: docparse 4/40, unstructured 2/40 |
| **Text completeness** | **unstructured slightly better, not significant** | word F1 0.840 vs 0.887; the 95% CIs overlap ([0.74, 0.93] vs [0.80, 0.96]) |
| **Speed** | **docparse ~13× faster** | 0.19 vs 2.40 s/page. unstructured's figure is dominated by the 92 image-only files sent to its OCR |
| **Structure** (14 tagged documents, 1,261 blocks) | **docparse ahead on most metrics; the confidence intervals overlap** | block recall 0.66 vs 0.62. Type accuracy 0.62 vs 0.60 (lenient 0.72 vs 0.64). Heading depth 0.33 vs 0.10. Reading order 0.988 vs 0.994 |
| **Tables** (ICDAR 2013, 157 tables) | **docparse; uncontested** | adjacency F1 0.73 vs 0.00. unstructured `fast` does no table structure at all, so this measures docparse against nothing, not against unstructured `hi_res` |

### What this does *not* show
- **docparse is not "much better" than unstructured.** On held-out data the structure gains are modest, with overlapping confidence intervals on 14 documents. Text completeness slightly favours unstructured.
- **Both parsers are weak on document structure in absolute terms.** Fewer than 2 in 3 tagged blocks are recovered intact, heading depth is ≤ 0.33, and section parent links are ≤ 0.13.
- **The development-set numbers (REALWORLD.md) overstated docparse.** This is the unbiased estimate.
- **unstructured `hi_res` and the hosted API were not tested**, so this is not a comparison with unstructured's best configuration.

### Failures found in docparse (reported, not fixed, per protocol)
- **Crashes on 5 malformed or encrypted files.** Broken hex/dictionary syntax, unsupported `/Crypt` filters, a truncated stream, and a print-protected file. The repair fallback only covers failures while *opening* the file, not later in parsing.
- **Text drawn with arbitrary rotation or skew** (`perf_text_transformed`) is shredded into thousands of fragments (word recall 0.09). Only 90° pages are handled.
- **Form-field values** (AcroForm widgets, e.g. a prefilled IRS 1040) are not extracted. unstructured `fast` misses them too.
- **One file** (`bug1019475_1`) yields no text where unstructured succeeds.

## Detail

### H1 Robustness: 300 random pdf.js corpus PDFs

| metric | docparse | unstructured |
|---|---|---|
| crashes | 5 / 300 | 5 / 300 |
| timeouts (>120 s) | 0 / 300 | 1 / 300 |
| silent failures (<10% of pdftotext words) | 4 / 40 | 2 / 40 |
| word F1 vs pdftotext (docs with ≥50 words), mean [95% CI] | 0.840 [0.740, 0.930] | 0.887 [0.804, 0.955] |
| seconds per page (successful docs) | 0.186 | 2.404 |

### H2 Structure: 14 tagged pdf.js PDFs (1261 tagged blocks)

Macro average over documents, mean [95% bootstrap CI]. depth_acc only on documents with heading tags.

| metric | docparse | unstructured |
|---|---|---|
| text_sim | 0.839 [0.740, 0.917] | 0.819 [0.732, 0.898] |
| block_recall | 0.663 [0.528, 0.792] | 0.619 [0.507, 0.730] |
| type_acc | 0.620 [0.489, 0.734] | 0.599 [0.464, 0.722] |
| type_acc_lenient | 0.715 [0.603, 0.804] | 0.637 [0.498, 0.763] |
| depth_acc | 0.327 [0.084, 0.608] | 0.098 [0.000, 0.232] |
| parent_acc | 0.126 [0.030, 0.224] | 0.082 [0.014, 0.167] |
| order | 0.988 [0.974, 0.997] | 0.994 [0.985, 1.000] |

<details><summary>per document</summary>

| document | pages | truth blocks | docparse recall / type / order | unstructured recall / type / order |
|---|---|---|---|---|
| PDFBOX-4417-001031 | 4 | 44 | 0.95 / 0.83 / 1.00 | 0.93 / 0.66 / 1.00 |
| PDFBOX-5762-722238 | 8 | 23 | 0.87 / 0.75 / 1.00 | 0.83 / 0.74 / 1.00 |
| PDFBOX-5792-240045 | 6 | 37 | 0.59 / 0.77 / 1.00 | 0.62 / 0.74 / 1.00 |
| PDFBOX-5809-509329 | 3 | 25 | 0.84 / 0.33 / 0.94 | 0.44 / 0.82 / 1.00 |
| PDFBOX-2725-878725 | 5 | 42 | 0.81 / 0.76 / 1.00 | 0.69 / 0.76 / 1.00 |
| 2023-06-20-PV | 2 | 30 | 0.90 / 0.30 / 1.00 | 0.77 / 0.52 / 1.00 |
| chelsea_pdta | 65 | 207 | 0.98 / 0.77 / 0.99 | 0.85 / 0.63 / 1.00 |
| cms-etc-filled | 18 | 62 | 0.60 / 0.57 / 0.97 | 0.48 / 0.47 / 0.97 |
| test_3727 | 2 | 32 | 0.41 / 0.92 / 1.00 | 0.34 / 0.91 / 1.00 |
| test_3842 | 34 | 213 | 0.87 / 0.73 / 1.00 | 0.81 / 0.94 / 1.00 |
| test_3848 | 4 | 131 | 0.40 / 0.12 / 0.93 | 0.51 / 0.15 / 0.95 |
| test_4965 | 2 | 185 | 0.18 / 0.68 / 1.00 | 0.40 / 0.55 / 0.99 |
| test_natural | 4 | 71 | 0.63 / 0.31 / 1.00 | 0.24 / 0.12 / 1.00 |
| form_dd0293 | 4 | 120 | 0.25 / 0.83 / 1.00 | 0.76 / 0.38 / 1.00 |

</details>

### H3 Tables: ICDAR 2013 competition, 66 documents, 157 ground-truth tables

| metric | docparse | unstructured |
|---|---|---|
| adjacency-relation F1 (ICDAR 2013 metric), mean [95% CI] | 0.732 [0.679, 0.785] | 0.000 [0.000, 0.000] |
| cell-grid accuracy, mean [95% CI] | 0.625 [0.561, 0.686] | 0.000 [0.000, 0.000] |
| tables detected (any correct relation) | 144 / 157 | 0 / 157 |
