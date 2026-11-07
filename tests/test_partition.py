import io
import json

import pytest

from docparse import (
    FileType,
    detect_filetype,
    elements_from_json,
    elements_to_bigquery_rows,
    elements_to_json,
    partition,
)
from docparse.partition.html import partition_html
from docparse.partition.md import partition_md
from docparse.partition.text import partition_text
from docparse.text_type import is_possible_narrative_text, is_possible_title


def cats(els):
    return [e.category for e in els]


def by_text(els, text):
    return next(e for e in els if e.text == text)


# ---------------------------------------------------------------- heuristics
@pytest.mark.parametrize("text,expected", [
    ("Introduction", True), ("3.2 Results and Discussion", True), ("1234", False),
    ("This is a full sentence that ends with a period.", False), ("Dear friends,", False), ("", False),
])
def test_is_possible_title(text, expected):
    assert is_possible_title(text) is expected


def test_is_possible_narrative_text():
    assert is_possible_narrative_text("The system processes documents and returns structured elements.")
    assert not is_possible_narrative_text("Quarterly Revenue Summary")
    assert not is_possible_narrative_text("12 34 56 78")


# ---------------------------------------------------------------- text
def test_partition_text_classifies_and_groups():
    text = ("EXECUTIVE SUMMARY\n\nThe company grew revenue by\n12% this year, which was\nabove plan.\n\n"
            "• lower costs\n• more customers\n\ninfo@example.com\n\n123 Main Street, Springfield, IL 62704\n")
    els = partition_text(text=text)
    assert cats(els) == ["Title", "NarrativeText", "ListItem", "ListItem", "EmailAddress", "Address"]
    assert els[1].text == "The company grew revenue by 12% this year, which was above plan."
    assert els[2].text == "lower costs"
    assert all(e.metadata.parent_id == els[0].id for e in els[1:])


def test_ids_are_deterministic():
    a = partition_text(text="Hello World\n\nThis is the body of the document text.", metadata_filename="x.txt")
    b = partition_text(text="Hello World\n\nThis is the body of the document text.", metadata_filename="x.txt")
    assert [e.id for e in a] == [e.id for e in b]
    assert len({e.id for e in a}) == len(a)


# ---------------------------------------------------------------- html
HTML = """<!doctype html><html lang="en"><head><title>x</title><style>p{}</style></head><body>
<header><p>Site banner</p></header>
<h1>Guide</h1>
<p>This guide explains how the parser turns <b>markup</b> into <a href="https://e.com">elements</a>.
<h2>Steps</h2>
<ul><li>Install it<ul><li>with pip</li></ul></li><li>Run it</li></ul>
<table><tr><th>Key</th><th>Value</th></tr><tr><td>a</td><td>1</td></tr></table>
<pre>code  here</pre>
<script>var x = 1;</script>
<div><span>Loose inline text that is long enough to be a narrative sentence for us.</span></div>
<footer>Copyright 2026</footer>
</body></html>"""


def test_partition_html():
    els = partition_html(text=HTML)
    assert cats(els) == ["Header", "Title", "NarrativeText", "Title", "ListItem", "ListItem", "ListItem",
                         "Table", "CodeSnippet", "NarrativeText", "Footer"]
    guide, steps = els[1], els[3]
    assert steps.metadata.parent_id == guide.id and steps.metadata.category_depth == 1
    para = els[2]
    assert para.metadata.link_urls == ["https://e.com"] and para.metadata.emphasized_text_contents == ["markup"]
    install, pip_item = els[4], els[5]
    assert pip_item.metadata.category_depth == 1 and pip_item.metadata.parent_id == install.id
    assert els[7].metadata.text_as_html == "<table><tr><td>Key</td><td>Value</td></tr><tr><td>a</td><td>1</td></tr></table>"
    assert els[8].text == "code  here"
    assert els[0].metadata.languages == ["en"]
    assert "var x" not in " ".join(e.text for e in els)


def test_html_unclosed_paragraphs():
    els = partition_html(text="<p>First paragraph is right here and it runs on.<p>Second paragraph is also right here now.")
    assert [e.text for e in els] == ["First paragraph is right here and it runs on.", "Second paragraph is also right here now."]


# ---------------------------------------------------------------- markdown
def test_partition_md():
    md = "---\ntitle: x\n---\nTitle\n=====\n\nSome *intro* text that describes the thing.\n\n### Deep\n\n1. one\n2. two\n\n    indented code\n"
    els = partition_md(text=md)
    assert cats(els) == ["Title", "NarrativeText", "Title", "ListItem", "ListItem", "CodeSnippet"]
    assert els[2].metadata.category_depth == 2 and els[2].metadata.parent_id == els[0].id
    assert els[1].text == "Some intro text that describes the thing."


# ---------------------------------------------------------------- office
def test_partition_docx(docx_file):
    els = partition(filename=docx_file, include_page_breaks=True)
    c = cats(els)
    assert c[0] == "Header" and els[0].text == "ACME Corp Confidential"
    report = by_text(els, "Annual Report")
    revenue = by_text(els, "Revenue")
    assert report.category == "Title" and report.metadata.category_depth == 0
    assert revenue.metadata.category_depth == 1 and revenue.metadata.parent_id == report.id
    bullets = [e for e in els if e.category == "ListItem"]
    assert [b.text for b in bullets] == ["North America grew fastest", "Europe was flat"]
    table = next(e for e in els if e.category == "Table")
    assert "<td>EMEA</td>" in table.metadata.text_as_html
    assert "PageBreak" in c
    outlook = by_text(els, "Outlook")
    assert outlook.metadata.page_number == 2 and outlook.metadata.parent_id is None
    bold = next(e for e in els if e.text.startswith("Bold statement"))
    assert bold.metadata.emphasized_text_contents == ["Bold statement here"]


def test_partition_pptx(pptx_file):
    els = partition(filename=pptx_file)
    assert by_text(els, "Product Launch").category == "Title"
    agenda = by_text(els, "Agenda")
    comp = by_text(els, "Competitors")
    assert comp.category == "ListItem" and comp.metadata.category_depth == 1
    assert comp.metadata.page_number == 2
    assert by_text(els, "Market overview").metadata.parent_id == agenda.id
    assert by_text(els, "Remember to mention the beta.").metadata.style_name == "speaker_notes"
    table = next(e for e in els if e.category == "Table")
    assert table.metadata.page_number == 3 and "Users" in table.text


def test_partition_xlsx(xlsx_file):
    els = partition(filename=xlsx_file)
    assert cats(els) == ["Table", "Table"]
    sales, notes = els
    assert sales.metadata.page_name == "Sales" and sales.metadata.page_number == 1
    assert sales.text.splitlines() == ["Region Units Price", "EMEA 10 2.5", "APAC 7 3"]
    assert notes.metadata.text_as_html == "<table><tr><td></td><td>only cell</td></tr></table>"


def test_partition_epub(epub_file):
    els = partition(filename=epub_file)
    assert cats(els) == ["Title", "NarrativeText", "Title", "NarrativeText"]
    assert [e.metadata.page_number for e in els] == [1, 1, 2, 2]
    assert els[0].metadata.languages == ["en"]


def test_partition_email(eml_file):
    els = partition(filename=eml_file)
    assert cats(els) == ["Title", "NarrativeText"]
    md = els[0].metadata
    assert md.subject == "Launch update" and md.sent_from == ["Alice <alice@example.com>"]
    assert md.sent_to == ["bob@example.com"] and md.email_message_id == "abc123@example.com"
    plain = partition(filename=eml_file, content_source="text/plain")
    assert plain[0].text == "Hi Bob,"


# ---------------------------------------------------------------- pdf
def test_partition_pdf(pdf_file):
    els = partition(filename=pdf_file)
    title = by_text(els, "A Study of Parsing")
    intro = by_text(els, "Introduction")
    assert title.category == "Title" and title.metadata.category_depth == 0
    assert intro.category == "Title" and intro.metadata.category_depth == 1
    assert intro.metadata.parent_id == title.id
    # repeating running header & page numbers detected as furniture
    assert {e.category for e in els if e.text == "Journal of Examples"} == {"Header"}
    assert [e.text for e in els if e.category == "Footer"] == ["1", "2"]
    # two-column reading order: whole left column before right column on each page
    p1 = [e.text for e in els if e.metadata.page_number == 1 and "column" in e.text]
    assert p1[0].startswith("Left column line 0") and p1[-1].endswith("line 5 has some words in it")
    assert "Left" in p1[0] and "Right" in p1[-1]
    assert all(lbl not in p1[0] for lbl in ("Right",))
    table = next(e for e in els if e.category == "Table")
    assert table.metadata.text_as_html == "<table><tr><td>Name</td><td>Score</td></tr><tr><td>alpha</td><td>1</td></tr><tr><td>beta</td><td>2</td></tr></table>"
    assert "alpha" not in " ".join(e.text for e in els if e.category != "Table")
    bullets = [e.text for e in els if e.category == "ListItem"]
    assert bullets == ["first bullet point", "second bullet point"]
    assert title.metadata.coordinates is not None and title.metadata.coordinates.layout_width == 612


# ---------------------------------------------------------------- csv / json / filetype
def test_partition_csv_and_tsv(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("a,b\n1,2\n")
    (el,) = partition(filename=str(p))
    assert el.category == "Table" and el.text == "a b\n1 2"
    (tel,) = partition(file=io.BytesIO(b"x\ty\n3\t4\n"), metadata_filename="t.tsv")
    assert tel.metadata.text_as_html == "<table><tr><td>x</td><td>y</td></tr><tr><td>3</td><td>4</td></tr></table>"


def test_json_round_trip(tmp_path):
    els = partition_html(text=HTML)
    path = tmp_path / "els.json"
    elements_to_json(els, filename=str(path))
    back = partition(filename=str(path))
    assert back == els and [e.id for e in back] == [e.id for e in els]
    assert elements_from_json(text=path.read_text()) == els


def test_detect_filetype(docx_file, pptx_file, xlsx_file, pdf_file, epub_file, eml_file):
    assert detect_filetype(docx_file) == FileType.DOCX
    assert detect_filetype(pptx_file) == FileType.PPTX
    assert detect_filetype(xlsx_file) == FileType.XLSX
    assert detect_filetype(pdf_file) == FileType.PDF
    assert detect_filetype(epub_file) == FileType.EPUB
    with open(eml_file, "rb") as f:
        assert detect_filetype(file=f) == FileType.EML
    assert detect_filetype(file=io.BytesIO(b"<html><body>x</body></html>")) == FileType.HTML


def test_bigquery_rows():
    els = partition_html(text=HTML, metadata_filename="guide.html")
    rows = elements_to_bigquery_rows(els)
    assert rows[1]["type"] == "Title" and rows[1]["filename"] == "guide.html"
    assert rows[3]["parent_id"] == rows[1]["element_id"]
    para = rows[2]
    assert json.loads(para["metadata"])["link_urls"] == ["https://e.com"]
    assert [r["sequence"] for r in rows] == list(range(len(rows)))
