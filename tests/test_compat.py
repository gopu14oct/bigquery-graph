"""docparse emits the same element schema as unstructured, so it is a drop-in replacement."""

import json

import pytest

from docparse import convert_to_dict, dict_to_elements, partition
from docparse.elements import ELEMENT_CLASSES

UNSTRUCTURED_TYPES = {
    "Title", "NarrativeText", "ListItem", "UncategorizedText", "Text", "Table", "TableChunk", "Header",
    "Footer", "Address", "EmailAddress", "FigureCaption", "Image", "Formula", "CodeSnippet", "PageBreak",
    "CompositeElement",
}
UNSTRUCTURED_METADATA = {
    "filename", "file_directory", "filetype", "last_modified", "url", "page_number", "page_name", "coordinates",
    "parent_id", "category_depth", "text_as_html", "languages", "emphasized_text_contents", "emphasized_text_tags",
    "link_texts", "link_urls", "header_footer_type", "sent_from", "sent_to", "cc_recipient", "subject",
    "email_message_id", "is_continuation", "orig_elements", "chunk_index",
}


def test_type_vocabulary_is_unstructureds():
    assert set(ELEMENT_CLASSES) <= UNSTRUCTURED_TYPES


def test_dict_schema(docx_file):
    for d in convert_to_dict(partition(filename=docx_file)):
        assert set(d) == {"type", "element_id", "text", "metadata"}
        assert set(d["metadata"]) - {"style_name"} <= UNSTRUCTURED_METADATA
        assert d["metadata"]["languages"] == ["eng"]


def test_strategy_kwarg(pdf_file):
    assert partition(filename=pdf_file, strategy="fast")


def test_reads_unstructured_json_and_vice_versa(docx_file):
    """Elements serialised by unstructured load into docparse, and the reverse."""
    unstructured = pytest.importorskip("unstructured.partition.auto")
    from unstructured.staging.base import elements_from_json as u_from_json

    u_els = unstructured.partition(filename=docx_file)
    u_json = json.dumps([e.to_dict() for e in u_els])
    ours = dict_to_elements(json.loads(u_json))
    assert [e.category for e in ours] == [e.category for e in u_els]
    assert [e.text for e in ours] == [e.text for e in u_els]
    back = u_from_json(text=json.dumps(convert_to_dict(partition(filename=docx_file))))
    assert len(back) > 0
