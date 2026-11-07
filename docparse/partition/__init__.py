from docparse.partition.auto import partition
from docparse.partition.email import partition_email
from docparse.partition.html import partition_html
from docparse.partition.md import partition_md
from docparse.partition.office import partition_docx, partition_epub, partition_pptx, partition_xlsx
from docparse.partition.pdf import partition_pdf
from docparse.partition.structured import partition_csv, partition_json, partition_tsv, partition_xml
from docparse.partition.text import partition_text

__all__ = [
    "partition", "partition_csv", "partition_docx", "partition_email", "partition_epub", "partition_html",
    "partition_json", "partition_md", "partition_pdf", "partition_pptx", "partition_text", "partition_tsv",
    "partition_xlsx", "partition_xml",
]
