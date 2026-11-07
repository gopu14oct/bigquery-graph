"""``partition()``: detect the file type and route to the right partitioner."""

from __future__ import annotations

from typing import Callable, Optional

from docparse.elements import Element
from docparse.filetype import FileType, detect_filetype
from docparse.partition.common import FileLike


def _registry() -> dict[FileType, Callable[..., list[Element]]]:
    from docparse.partition.email import partition_email
    from docparse.partition.html import partition_html
    from docparse.partition.md import partition_md
    from docparse.partition.office import partition_docx, partition_epub, partition_pptx, partition_xlsx
    from docparse.partition.pdf import partition_pdf
    from docparse.partition.structured import partition_csv, partition_json, partition_tsv, partition_xml
    from docparse.partition.text import partition_text

    return {
        FileType.PDF: partition_pdf, FileType.DOCX: partition_docx, FileType.PPTX: partition_pptx,
        FileType.XLSX: partition_xlsx, FileType.EPUB: partition_epub, FileType.HTML: partition_html,
        FileType.MD: partition_md, FileType.TXT: partition_text, FileType.CSV: partition_csv,
        FileType.TSV: partition_tsv, FileType.JSON: partition_json, FileType.XML: partition_xml,
        FileType.EML: partition_email,
    }


def partition(
    filename: Optional[str] = None,
    file: Optional[FileLike] = None,
    content_type: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    **kwargs,
) -> list[Element]:
    """Partition any supported document into elements.

    Extra ``kwargs`` are forwarded to the format-specific partitioner (e.g.
    ``include_page_breaks``, ``languages``, ``metadata_filename``) and, when
    ``chunking_strategy`` is given (``"basic"`` or ``"by_title"``), to the chunker
    (``max_characters``, ``new_after_n_chars``, ``overlap``...).
    """
    if filename is None and file is None:
        raise ValueError("Either `filename` or `file` must be provided.")
    # For streams, the metadata filename's extension is the best type hint we have.
    ftype = detect_filetype(filename=filename or kwargs.get("metadata_filename"), file=file, content_type=content_type)
    fn = _registry().get(ftype)
    if fn is None:
        raise ValueError(f"Unsupported file type: {ftype.value} ({filename or 'stream'})")

    from docparse.chunking import CHUNK_KWARGS, chunk

    chunk_kwargs = {k: kwargs.pop(k) for k in list(kwargs) if k in CHUNK_KWARGS}
    elements = fn(filename=filename, file=file, **kwargs)
    if chunking_strategy:
        elements = chunk(elements, chunking_strategy, **chunk_kwargs)
    return elements
