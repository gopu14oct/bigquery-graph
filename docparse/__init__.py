"""docparse - a from-scratch document partitioning & chunking library.

Modelled on the ideas behind Unstructured.io: any document -> a flat list of typed
elements (Title, NarrativeText, ListItem, Table, ...) with rich metadata, plus
chunkers that turn those elements into retrieval-ready chunks.
"""

from docparse.chunking import chunk, chunk_by_title, chunk_elements
from docparse.elements import *  # noqa: F401,F403
from docparse.filetype import FileType, detect_filetype
from docparse.partition import *  # noqa: F401,F403
from docparse.staging import (
    convert_to_dataframe,
    convert_to_dict,
    dict_to_elements,
    elements_from_json,
    elements_to_bigquery_rows,
    elements_to_dataframe,
    elements_to_json,
    elements_to_jsonl,
    elements_to_text,
)

__version__ = "0.1.0"
