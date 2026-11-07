import pytest

from docparse.chunking import chunk_by_title, chunk_elements, split_text
from docparse.elements import ElementMetadata, ListItem, NarrativeText, Table, Title
from docparse.partition.html import rows_to_html, rows_to_text


def md(page=1, **kw):
    return ElementMetadata(filename="f.txt", page_number=page, **kw)


def test_split_text_prefers_sentence_boundaries():
    text = "One two three. Four five six. Seven eight nine."
    parts = split_text(text, 30)
    assert parts == ["One two three. Four five six.", "Seven eight nine."]
    assert all(len(p) <= 50 for p in split_text("word " * 200, 50, overlap=10))


def test_split_text_overlap():
    parts = split_text("aaaa bbbb cccc dddd eeee ffff", 15, overlap=5)
    assert all(len(p) <= 15 for p in parts)
    assert parts[1].split()[0] in parts[0]


def test_basic_fills_up_to_max():
    els = [NarrativeText("x" * 100, md()) for _ in range(5)]
    chunks = chunk_elements(els, max_characters=250)
    assert [len(c.text) for c in chunks] == [202, 202, 100]
    assert all(c.category == "CompositeElement" for c in chunks)
    assert len(chunks[0].metadata.orig_elements) == 2
    assert [c.metadata.chunk_index for c in chunks] == [0, 1, 2]


def test_soft_limit():
    els = [NarrativeText("x" * 100, md()) for _ in range(4)]
    chunks = chunk_elements(els, max_characters=1000, new_after_n_chars=150)
    assert [len(c.text) for c in chunks] == [202, 202]


def test_by_title_respects_sections_and_combines_small_ones():
    els = [Title("A", md()), NarrativeText("a" * 50, md()), Title("B", md()), NarrativeText("b" * 50, md()),
           Title("C", md()), NarrativeText("c" * 300, md())]
    chunks = chunk_by_title(els, max_characters=400, combine_text_under_n_chars=0)
    assert [c.text.split("\n\n")[0] for c in chunks] == ["A", "B", "C"]
    combined = chunk_by_title(els, max_characters=400, combine_text_under_n_chars=200)
    assert [c.text.split("\n\n")[0] for c in combined] == ["A", "C"]
    assert combined[0].text == "A\n\n" + "a" * 50 + "\n\nB\n\n" + "b" * 50


def test_by_title_multipage_sections_false():
    els = [Title("A", md(1)), NarrativeText("one", md(1)), NarrativeText("two", md(2))]
    assert len(chunk_by_title(els, multipage_sections=True)) == 1
    assert len(chunk_by_title(els, multipage_sections=False, combine_text_under_n_chars=0)) == 2


def test_oversized_element_is_split_with_continuation():
    text = " ".join(f"Sentence number {i} is here." for i in range(40))
    chunks = chunk_elements([NarrativeText(text, md())], max_characters=200, overlap=20)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)
    assert chunks[0].metadata.is_continuation is None
    assert all(c.metadata.is_continuation for c in chunks[1:])


def test_overlap_all_stays_within_max():
    els = [NarrativeText(f"Paragraph {i} " + "word " * 30, md()) for i in range(6)]
    chunks = chunk_elements(els, max_characters=200, overlap=30, overlap_all=True)
    assert all(len(c.text) <= 200 for c in chunks)
    assert chunks[1].text.startswith("word")


def test_tables_isolated_and_split_by_rows():
    rows = [["id", "name"]] + [[str(i), f"name-{i}"] for i in range(50)]
    tbl = Table(rows_to_text(rows), md(text_as_html=rows_to_html(rows)))
    els = [NarrativeText("before", md()), tbl, NarrativeText("after", md())]
    chunks = chunk_elements(els, max_characters=100)
    assert chunks[0].text == "before" and chunks[-1].text == "after"
    tchunks = chunks[1:-1]
    assert {c.category for c in tchunks} == {"TableChunk"}
    assert all(len(c.text) <= 100 for c in tchunks)
    assert all(c.metadata.text_as_html.startswith("<table><tr>") for c in tchunks)
    assert tchunks[0].metadata.is_continuation is None and tchunks[1].metadata.is_continuation
    joined = "\n".join(c.text for c in tchunks)
    assert joined == tbl.text


def test_small_table_stays_table():
    tbl = Table("a b", md(text_as_html="<table><tr><td>a</td><td>b</td></tr></table>"))
    (c,) = chunk_elements([tbl])
    assert c.category == "Table" and c.metadata.text_as_html


def test_metadata_consolidation():
    els = [ListItem("x", md(link_urls=["u1"], link_texts=["t1"], languages=["en"], category_depth=2)),
           ListItem("y", md(page=2, link_urls=["u2"], link_texts=["t2"], languages=["fr"]))]
    (c,) = chunk_elements(els)
    assert c.metadata.page_number == 1
    assert c.metadata.link_urls == ["u1", "u2"] and c.metadata.languages == ["en", "fr"]
    assert c.metadata.category_depth is None


def test_invalid_options():
    with pytest.raises(ValueError):
        chunk_elements([], max_characters=0)
    with pytest.raises(ValueError):
        chunk_elements([], max_characters=10, overlap=10)
