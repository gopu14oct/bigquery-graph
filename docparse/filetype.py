"""File type detection from content (magic bytes / ZIP manifest / text sniffing),
falling back to the file extension."""

from __future__ import annotations

import enum
import io
import os
import zipfile
from typing import Optional


class FileType(str, enum.Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    EPUB = "epub"
    HTML = "html"
    MD = "md"
    TXT = "txt"
    CSV = "csv"
    TSV = "tsv"
    JSON = "json"
    XML = "xml"
    EML = "eml"
    ZIP = "zip"
    UNKNOWN = "unknown"


EXT_TO_FILETYPE = {
    ".pdf": FileType.PDF, ".docx": FileType.DOCX, ".pptx": FileType.PPTX, ".xlsx": FileType.XLSX,
    ".epub": FileType.EPUB, ".html": FileType.HTML, ".htm": FileType.HTML, ".xhtml": FileType.HTML,
    ".md": FileType.MD, ".markdown": FileType.MD, ".txt": FileType.TXT, ".text": FileType.TXT,
    ".log": FileType.TXT, ".csv": FileType.CSV, ".tsv": FileType.TSV, ".json": FileType.JSON,
    ".ndjson": FileType.JSON, ".jsonl": FileType.JSON, ".xml": FileType.XML, ".eml": FileType.EML,
}


def _sniff_zip(data: bytes) -> FileType:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return FileType.UNKNOWN
    names = set(z.namelist())
    if "mimetype" in names and z.read("mimetype").strip() == b"application/epub+zip":
        return FileType.EPUB
    if "word/document.xml" in names:
        return FileType.DOCX
    if "ppt/presentation.xml" in names:
        return FileType.PPTX
    if "xl/workbook.xml" in names:
        return FileType.XLSX
    return FileType.ZIP


def _sniff_text(head: str) -> FileType:
    s = head.lstrip().lower()
    first = head.splitlines()[:30]
    header_keys = ("from:", "to:", "subject:", "date:", "message-id:", "mime-version:", "received:", "return-path:")
    if sum(1 for ln in first if ln.lower().startswith(header_keys)) >= 2:
        return FileType.EML
    if s.startswith(("<!doctype html", "<html")) or ("<body" in s[:2000] and "</" in s):
        return FileType.HTML
    if s.startswith("<?xml") or (s.startswith("<") and not s.startswith("<!--")):
        return FileType.HTML if "<html" in s[:2000] else FileType.XML
    if s.startswith(("[", "{")):
        return FileType.JSON
    return FileType.TXT


def detect_filetype(filename: Optional[str] = None, file=None, content_type: Optional[str] = None) -> FileType:
    ext_type = None
    if filename:
        ext_type = EXT_TO_FILETYPE.get(os.path.splitext(filename)[1].lower())
    if content_type:
        mapping = {"application/pdf": FileType.PDF, "text/html": FileType.HTML, "text/markdown": FileType.MD,
                   "text/plain": FileType.TXT, "text/csv": FileType.CSV, "message/rfc822": FileType.EML}
        if content_type in mapping:
            return mapping[content_type]

    if filename and os.path.exists(filename):
        with open(filename, "rb") as f:
            data = f.read()
    elif file is not None:
        pos = file.tell() if hasattr(file, "tell") else 0
        data = file.read()
        if hasattr(file, "seek"):
            file.seek(pos)
        if isinstance(data, str):
            data = data.encode("utf-8")
    else:
        return ext_type or FileType.UNKNOWN

    if data.startswith(b"%PDF"):
        return FileType.PDF
    if data.startswith(b"PK\x03\x04"):
        return _sniff_zip(data)
    head_bytes = data[:8192]
    if b"\x00" in head_bytes and not head_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        return ext_type or FileType.UNKNOWN
    # Extension is a better signal than sniffing for these lightweight text formats.
    if ext_type in (FileType.MD, FileType.CSV, FileType.TSV, FileType.TXT, FileType.JSON, FileType.EML, FileType.XML, FileType.HTML):
        return ext_type
    return _sniff_text(head_bytes.decode("utf-8", errors="ignore"))
