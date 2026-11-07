"""RFC 822 / MIME email partitioner (.eml) using the stdlib ``email`` package."""

from __future__ import annotations

import email
import email.policy
from email.utils import getaddresses, parsedate_to_datetime
from typing import Optional

from docparse.elements import Element
from docparse.partition.common import FileLike, base_metadata, finalize, read_bytes
from docparse.partition.html import html_to_elements
from docparse.partition.text import text_to_elements


def _addrs(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    out = [f"{n} <{a}>" if n else a for n, a in getaddresses([value])]
    return out or None


def partition_email(
    filename: Optional[str] = None,
    file: Optional[FileLike] = None,
    text: Optional[str] = None,
    content_source: str = "text/html",
    include_headers: bool = False,
    metadata_filename: Optional[str] = None,
    languages: Optional[list[str]] = None,
    unique_element_ids: bool = False,
    **_: object,
) -> list[Element]:
    """Partition the email body. ``content_source`` picks the preferred MIME part
    (``text/html`` or ``text/plain``); the other one is used as a fallback."""
    raw = text.encode("utf-8") if text is not None else read_bytes(filename, file)
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    last_modified = None
    if msg["date"]:
        try:
            last_modified = parsedate_to_datetime(str(msg["date"])).isoformat()
        except (TypeError, ValueError):
            pass
    base = base_metadata(filename, "message/rfc822", metadata_filename, last_modified=last_modified)
    base.sent_from = _addrs(msg["from"])
    base.sent_to = _addrs(msg["to"])
    base.cc_recipient = _addrs(msg["cc"])
    base.subject = str(msg["subject"]) if msg["subject"] else None
    base.email_message_id = str(msg["message-id"]).strip("<>") if msg["message-id"] else None

    order = ("html", "plain") if content_source == "text/html" else ("plain", "html")
    body = msg.get_body(preferencelist=order)
    elements: list[Element] = []
    if body is not None:
        content = body.get_content()
        if body.get_content_subtype() == "html":
            elements = html_to_elements(content, base.copy())
        else:
            elements = text_to_elements(content, base.copy())
    if include_headers:
        from docparse.elements import Text

        header_els = [Text(f"{k}: {v}", base.copy()) for k, v in msg.items() if k.lower() in ("from", "to", "cc", "subject", "date")]
        elements = header_els + elements
    return finalize(elements, base, languages, unique_element_ids=unique_element_ids)
