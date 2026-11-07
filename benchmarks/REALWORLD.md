# Real-world PDF evaluation: docparse vs. unstructured 0.27.37

The synthetic benchmark ([README.md](README.md)) gives exact answers, but its documents are generated. This page
covers **real PDFs**: 1,480 pages in 19 documents, plus 9 real table PDFs. Accuracy is measured three ways.

| answer key | what it measures | source |
|---|---|---|
| Poppler `pdftotext` | text completeness (word F1); order-insensitive, independent third extractor | [RESULTS_realworld.md](RESULTS_realworld.md) |
| **PDF accessibility tags** | headings, paragraphs, lists, tables and reading order, as authored by Word/InDesign | [RESULTS_tagged.md](RESULTS_tagged.md) |
| **camelot test suite** | exact table cell contents for real government/statistics PDFs | [RESULTS_tables.md](RESULTS_tables.md) |

For scanned pages we rasterised born-digital PDFs ourselves, so the original text layer is an exact answer key.

## Corpus

Documents come from unstructured's own `example-docs`, `py-pdf/sample-files`, pypdf and ocrmypdf test resources, and camelot's tests:

| document | pages | what makes it hard |
|---|---|---|
| DA-619p | 619 | OpenOffice book, dot-leader table of contents |
| irs-400p | 400 | XPP/Distiller government manual, 117 images |
| nccn-137p | 137 | InDesign clinical guideline: flowcharts, footnotes, 148 images, numbered references |
| geotopo-117p | 117 | German LaTeX textbook with maths and figures |
| breast-cancer-76p | 76 | InDesign brochure: sidebars, images |
| geobase-uml-19p | 19 | Word spec: UML diagrams, abbreviation list, tables |
| layout-parser / multi-column / fr-olap / conr | 11–16 | LaTeX papers: two columns, figures, rotated arXiv stamp; one has a **malformed PDF structure** |
| 4 × scan12p | 48 | 200-dpi grayscale scans of real papers and brochures |
| camelot tables | 9 files | ruled, borderless, rotated 90°, Arabic, two tables per page |

## Results (final code)

| | docparse | unstructured (`fast` / `ocr_only`) |
|---|---|---|
| documents parsed without crash | 19/19 | 19/19 |
| speed (all 1,480 pages) | **0.16 s/page** (231 s) | 0.38 s/page (561 s) |
| peak memory | **370 MB** | 998 MB |
| text completeness, median word F1 (born-digital) | **0.986** | 0.966 |
| OCR word F1 on scans (mean of 4) | 0.938 | 0.938 |
| table cells, camelot answer key (mean of 11 tables) | **0.567** | 0.000 |
| tagged NCCN (1,714 blocks): blocks recovered intact | **0.658** | 0.543 |
| tagged NCCN: section parent correct | **0.187** | 0.059 |
| tagged Word spec (73 blocks): blocks recovered intact | **0.662** | 0.620 |
| tagged brochure (489 blocks): blocks recovered intact | **0.800** | 0.605 |
| tagged brochure: block type correct | 0.824 | **0.909** |
| heading depth vs. tags (NCCN) | 0.098 | **0.103** |

### Where unstructured is better
- **Block types on the InDesign brochure** (0.91 vs 0.82). The tags label numbered references and some bold lead-ins as plain P, where docparse says ListItem or Title.
- **Recall on the maths-heavy LaTeX textbook** (word recall 0.92 vs 0.84). docparse loses more maths glyphs.
- **OCR of a scanned Word spec with UML diagrams** (word F1 0.91 vs 0.88).

### Where both are weak (true deficiencies, not answer-key noise)
- **Heading depth on real documents is about 0.10 for both.** The synthetic benchmark (0.92) overstates docparse badly here. Real documents use the same font size for several heading levels and number their sections inconsistently, and font size alone cannot recover the hierarchy that the tags hold.
- **Section `parent_id` on real documents is low** (≤ 0.37) for the same reason.
- **Borderless tables:** docparse scores 0.42 on the health-budget table and ≈ 0 on the census and rotated borderless pages. Those tables come out split into fragments and lose their multi-line header rows. unstructured `fast` extracts no tables at all; its `hi_res` (not testable here) is designed for this.
- **Diagram text** (UML boxes with letters stacked vertically) comes out letter-by-letter in both parsers.
- **Images:** docparse emits `Image` elements with position and links "Figure N" captions to them, but describes nothing inside the image (there is no image model). unstructured `fast` emits no images.

## What was fixed in docparse because of the real documents

Every fix is general: none is keyed to a document name or text.
- **Word splitting is now relative to font size** (`x_tolerance_ratio=0.15`). Tightly set LaTeX text used to come out glued ("Open-domainquestionanswering"). Word F1 on the two-column paper went from 0.59 to 0.99, and on the French paper from 0.56 to 0.94.
- **Repair-and-retry** with pypdf for PDFs pdfminer cannot open. `conr-11p` used to crash.
- **Memory:** releasing each page's pdfminer cache cut peak memory on 619 pages from 3.7 GB to 342 MB.
- **Rotated text:** sidebar stamps and labels are rebuilt from glyphs, with the reading direction taken from the glyph matrix.
- **Sideways (landscape) pages are rotated back** before layout analysis, and the real font size comes from the glyph matrix. Rotated ruled tables went from 0.02 to 0.96.
- **Borderless table detection** from text alignment, with partial ruled grids cleaned or rejected. Health-budget table: 0.06 → 0.42.
- **Numbered list items keep their number** ("124. Newman KD…"). Wrapped lines of references that return to the left margin stay in the same item.
- **Running heads** are recognised by repetition within the top/bottom 15% of the page.
- **Abbreviation/glossary rows** ("LRM   Linear Reference Method"): runs of three or more consecutive label/value lines are joined and not read as columns. A first, looser version of this rule **regressed the synthetic two-column PDFs** (text similarity fell to 0.51). The regression was caught by re-running the synthetic benchmark and fixed before these numbers were taken.
- **Section numbers stay attached** to their heading ("1 Overview").
- **Not headings any more:** table-of-contents lines with dot leaders, Roman page numbers, URLs and e-mails.
- **Figures:** `Image` elements, `FigureCaption` detection, and caption-to-image linking.

## Caveats: read before quoting

1. **These real documents were also used to find and fix bugs.** After the first measurement, I inspected the tagged documents and the camelot tables and fixed what they exposed. That makes them a development set: the "after" numbers above are optimistic for docparse. The first-run (pre-fix) numbers were worse:
   - tagged NCCN: block recall 0.38
   - Word spec: block recall 0.35
   - camelot tables: 0.35
   
   A fresh, untouched set of real documents is needed for an unbiased number.
2. **Accessibility tags are an imperfect answer key.** Exporters tag some visual headings as P; the InDesign brochure has no heading tags at all; and numbered references are tagged P where docparse emits ListItem. Type accuracy is partly a matter of convention. Recall and order are the most trustworthy tag metrics.
3. **The unstructured runs are `fast` (born-digital) and `ocr_only` (scans).** `hi_res`, which adds a layout model and table structure recognition, could not run here (model download blocked). It would likely close much of the table and heading gap. **This is not a comparison against unstructured's best configuration.**
4. Single run on a 4-core cloud container. Treat timings as relative.

## Reproduce

```bash
python benchmarks/realworld.py build --out /tmp/real          # needs the example-doc repos cloned (see CORPUS paths)
python benchmarks/realworld.py run --out /tmp/real --parser docparse
VENV/python benchmarks/realworld.py run --out /tmp/real --parser unstructured --python VENV/python
python benchmarks/tagged_truth.py --corpus /tmp/real/corpus --out /tmp/real/truth nccn-137p geobase-uml-19p breast-cancer-76p
python benchmarks/realworld.py report --out /tmp/real
python benchmarks/score_tagged.py --real /tmp/real
python benchmarks/tables_camelot.py build --out /tmp/tables && python benchmarks/realworld.py run --out /tmp/tables --parser docparse && \
  VENV/python benchmarks/realworld.py run --out /tmp/tables --parser unstructured --python VENV/python && \
  python benchmarks/tables_camelot.py score --out /tmp/tables
```
