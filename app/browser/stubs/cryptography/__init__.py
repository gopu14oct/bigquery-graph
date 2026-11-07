"""Stand-in for `cryptography` in the browser build.

pdfminer imports it at module load, but only uses it to decrypt AES-encrypted PDFs.
The real package is a native extension, so the browser build ships this stub:
everything parses except AES-encrypted files, which raise a clear error.
"""
