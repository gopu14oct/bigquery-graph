# docparse: a from-scratch, Unstructured.io-style document parser

`docparse` turns PDFs, Word, PowerPoint, Excel, HTML, Markdown, e-mail, EPUB, CSV and plain text into
**one flat list of typed elements** with rich metadata. It also **chunks** them into pieces sized for
retrieval/RAG and **stages** them as rows for JSON, pandas or BigQuery.

It is a clean-room re-implementation of the *ideas* behind [Unstructured.io](https://unstructured.io)'s
open-source library. No code is copied and the `unstructured` package is not needed.

---

## 1. Research: how Unstructured.io works

Unstructured's core abstraction is **"partition, then chunk"**:

| Concept | What Unstructured does | What docparse does |
|---|---|---|
| **Element** | Each document becomes a flat `list[Element]`. The class is the category (`Title`, `NarrativeText`, `ListItem`, `Table`, `Header`, `Footer`, `Image`, `PageBreak`, `Address`, `EmailAddress`, `FigureCaption`, `Formula`, `CodeSnippet`, `UncategorizedText`…) | Same categories (`docparse/elements.py`) |
| **Metadata** | `filename`, `filetype`, `page_number`, `parent_id`, `category_depth`, `text_as_html`, `coordinates`, `languages`, `emphasized_text_*`, `link_*`, `last_modified`, email fields, `orig_elements`, `is_continuation`… | Same fields; `None` values are left out when serialised |
| **Element ID** | Deterministic: `sha256(filename + text + page_number + seq_on_page)[:32]` | Same idea, with the category added to the key. Ids are stable, so a re-parsed document can be upserted |
| **Hierarchy** | Not nested. Each element points to its parent with `parent_id`, and `category_depth` gives the heading or list level | `assign_hierarchy()` in `partition/common.py` |
| **Partitioners** | `partition_<fmt>()` for each file type, plus `partition()` (auto), which detects the type with libmagic and the extension | Hand-written parser for each format; detection from magic bytes and the ZIP manifest (`filetype.py`) |
| **Text typing** | Rules without ML for unmarked text: capitalisation ratio, non-alpha ratio, word count, verb check (NLTK), bullet regexes | The same kinds of rules without NLTK: a function-word and verb-suffix check takes the place of POS tagging (`text_type.py`) |
| **PDF strategies** | `fast` (pdfminer text), `hi_res` (layout model + OCR), `ocr_only`, `vlm` | One **layout-aware fast** strategy: our own line, block, XY-cut, font-size and furniture analysis on pdfminer glyph boxes. No OCR or vision model |
| **Chunking** | `basic` and `by_title`, with `max_characters` (hard limit, default 500), `new_after_n_chars` (soft limit), `combine_text_under_n_chars`, `multipage_sections`, `overlap`, `overlap_all`. Tables are kept apart and split into `TableChunk`s. The output is `CompositeElement`, `Table` or `TableChunk`, with `orig_elements` | Same options, defaults and output types (`chunking.py`) |
| **Staging** | `to_dict`, JSON, CSV, DataFrame, plus connectors to many destinations | JSON, JSONL, DataFrame and **BigQuery rows/schema/loader** (`staging.py`) |

## 2. Architecture

```
file ─► detect_filetype ─► partition_<fmt> ─► finalize ─► [chunk] ─► staging
          (magic bytes,      (format-specific    (base metadata,   (basic /     (JSON, JSONL,
           ZIP manifest,      parsing → raw       stable ids,       by_title)    DataFrame,
           text sniffing)     elements)           parent_id tree)                BigQuery)
```

| Module | Responsibility |
|---|---|
| `elements.py` | Element classes, `ElementMetadata`, `Coordinates`, (de)serialisation |
| `text_type.py` | Rules that decide whether a string is a title, prose, a bullet, an e-mail address, a postal address or a page number |
| `cleaners.py` | Whitespace, bullets, dashes, quotes, ligatures, de-hyphenation, re-joining broken paragraphs |
| `filetype.py` | Content-based type detection |
| `partition/text.py` | Paragraph splitting (handles hard-wrapped text and bullet runs) |
| `partition/html.py` | **Own DOM** built on the stdlib `html.parser` tokenizer: closes implied `<p>`/`<li>` tags, handles nested lists, tables with colspan, skips script/style, keeps emphasis and links |
| `partition/md.py` | **Own Markdown block parser**: ATX/setext headings, fenced and indented code, nested lists, GFM tables, images, front matter |
| `partition/office.py` | **DOCX / PPTX / XLSX / EPUB read straight from ZIP+XML** with `zipfile` + `ElementTree`: heading levels from the style chain, numbering and list depth, page breaks, headers and footers, hyperlinks from relationship files, slide placeholders and notes, shared strings, the EPUB spine |
| `partition/pdf.py` | **Own layout analysis** on positioned words: line clustering, block building, recursive XY-cut for multi-column reading order, heading levels ranked by font size, bold-heading detection, repeated header/footer detection, ruled tables (cell grid from pdfplumber), re-attaching bullet glyphs, `(cid:N)` repair, coordinates |
| `partition/email.py` | MIME parsing with the stdlib `email` package; prefers the HTML or the plain-text body; sender, recipients, subject and message id |
| `partition/structured.py` | CSV/TSV (delimiter sniffing) → `Table`; JSON round-trip; XML leaf nodes |
| `chunking.py` | `basic` and `by_title` chunkers, the text splitter, table row splitting, metadata merging |
| `staging.py` | JSON/JSONL/DataFrame/BigQuery |

**Dependencies.** The core needs only the Python standard library. PDF needs `pdfplumber`, which wraps
`pdfminer.six`. We use it only to decode content streams, fonts and encodings into word boxes and ruled-table
grids. Every layout decision after that is made in `pdf.py`.

## 3. Usage

```bash
pip install -e ".[pdf]"          # from the repo root
docparse report.pdf --format summary
docparse report.docx --chunking-strategy by_title --max-characters 1000 --format jsonl -o chunks.jsonl
docparse deck.pptx --format bigquery -o rows.jsonl
```

```python
from docparse import partition, chunk_by_title, elements_to_dataframe

elements = partition("report.pdf")                      # auto-detects the type
for el in elements[:5]:
    print(el.category, el.metadata.page_number, el.text[:60])

titles = [e for e in elements if e.category == "Title"]
children = [e for e in elements if e.metadata.parent_id == titles[0].id]

chunks = chunk_by_title(elements, max_characters=1000, new_after_n_chars=800,
                        combine_text_under_n_chars=200, overlap=100)
df = elements_to_dataframe(chunks)

# or partition and chunk in one call
chunks = partition("page.html", chunking_strategy="by_title", max_characters=1500)
```

### Chunking options

| option | default | meaning |
|---|---|---|
| `max_characters` | 500 | hard limit. No chunk is longer than this |
| `new_after_n_chars` | = max | soft limit. Start a new chunk once this size is reached |
| `combine_text_under_n_chars` | = max (`by_title` only) | merge a small section with the next one when the result still fits |
| `multipage_sections` | True (`by_title` only) | when False, a page change also starts a new chunk |
| `overlap` | 0 | number of characters repeated between pieces of a split oversized element |
| `overlap_all` | False | also apply `overlap` between ordinary chunks (still within `max_characters`) |
| `include_orig_elements` | True | keep the source elements in `metadata.orig_elements` |

## 4. Into BigQuery

`elements_to_bigquery_rows()` matches `staging.BIGQUERY_SCHEMA`. Fields you filter on often become columns
(`type`, `page_number`, `parent_id`, `category_depth`, `filename`…). Everything else goes into a `JSON`
column. `load_to_bigquery(elements, "project.dataset.doc_elements")` loads the rows with the official client.

Each element row has a `parent_id`, so the document tree is an edge list. This fits the **BigQuery
Property Graph** tutorial in this repo: elements are nodes, and `parent_id → element_id` gives `CHILD_OF` edges.
The SQL below is a sketch and has not been run against a live BigQuery project.

```sql
CREATE PROPERTY GRAPH docs.DocGraph
  NODE TABLES (docs.doc_elements KEY (element_id))
  EDGE TABLES (
    docs.doc_elements AS ChildOf KEY (element_id)
      SOURCE KEY (element_id) REFERENCES doc_elements (element_id)
      DESTINATION KEY (parent_id) REFERENCES doc_elements (element_id)
  );

-- every paragraph under the "Revenue" section, at any depth
GRAPH docs.DocGraph
MATCH (p:doc_elements)-[:ChildOf]->{1,5}(s:doc_elements)
WHERE s.type = 'Title' AND s.text = 'Revenue'
RETURN p.type, p.text;
```

## 5. Limitations compared with Unstructured's hosted and `hi_res` pipeline

- **No OCR or vision layout model.** Scanned PDFs and images produce no text. You could add an OCR step
  (for example Tesseract) as another partitioner.
- PDF tables are detected only when they have ruling lines. Tables made from whitespace alignment come out as text.
- Legacy binary formats (`.doc`, `.ppt`, `.xls`, `.msg`) are not supported. Convert them with LibreOffice first.
- Language detection is not done. Pass `languages=[...]`, or rely on HTML `lang` and EPUB `dc:language`.

## 6. Tests

```bash
pip install -e ".[test]" && pytest
```

The fixtures are built on the fly with python-docx, python-pptx, openpyxl and reportlab, which are used
**only** to *create* sample files. The tests cover every partitioner, the heading hierarchy, two-column
PDF reading order, header/footer detection, table extraction, chunk limits and overlap, table row
splitting, the JSON round-trip and the BigQuery rows.
