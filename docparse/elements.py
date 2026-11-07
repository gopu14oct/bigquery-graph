"""Document element model.

Every partitioner turns a file into a flat, ordered ``list[Element]``. Each element
has a *category* (its class), its ``text`` and an ``ElementMetadata`` record. The
hierarchy of the document is not encoded by nesting but by ``metadata.parent_id``
and ``metadata.category_depth`` so the list can be serialised straight to rows
(JSON, a DataFrame, a BigQuery table...).
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Optional


@dataclass
class Coordinates:
    """Bounding box of an element on its page (PDF only).

    ``points`` are the four corners, clockwise from the top-left, in the
    coordinate ``system`` (points, origin top-left, y grows downwards).
    """

    points: tuple[tuple[float, float], ...]
    system: str = "PixelSpace"
    layout_width: Optional[float] = None
    layout_height: Optional[float] = None

    @classmethod
    def from_bbox(cls, x0, top, x1, bottom, width=None, height=None) -> "Coordinates":
        pts = ((x0, top), (x0, bottom), (x1, bottom), (x1, top))
        return cls(points=tuple((round(x, 2), round(y, 2)) for x, y in pts),
                   layout_width=width, layout_height=height)

    def to_dict(self) -> dict:
        return {
            "points": [list(p) for p in self.points],
            "system": self.system,
            "layout_width": self.layout_width,
            "layout_height": self.layout_height,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Coordinates":
        return cls(points=tuple(tuple(p) for p in d["points"]), system=d.get("system", "PixelSpace"),
                   layout_width=d.get("layout_width"), layout_height=d.get("layout_height"))


@dataclass
class ElementMetadata:
    """Metadata attached to every element. ``None`` fields are omitted on serialisation."""

    # --- provenance ---
    filename: Optional[str] = None
    file_directory: Optional[str] = None
    filetype: Optional[str] = None  # MIME type
    last_modified: Optional[str] = None
    url: Optional[str] = None

    # --- position ---
    page_number: Optional[int] = None
    page_name: Optional[str] = None  # e.g. worksheet name
    coordinates: Optional[Coordinates] = None

    # --- hierarchy ---
    parent_id: Optional[str] = None
    category_depth: Optional[int] = None

    # --- content enrichments ---
    text_as_html: Optional[str] = None
    languages: Optional[list[str]] = None
    emphasized_text_contents: Optional[list[str]] = None
    emphasized_text_tags: Optional[list[str]] = None
    link_texts: Optional[list[str]] = None
    link_urls: Optional[list[str]] = None
    header_footer_type: Optional[str] = None
    style_name: Optional[str] = None

    # --- email ---
    sent_from: Optional[list[str]] = None
    sent_to: Optional[list[str]] = None
    cc_recipient: Optional[list[str]] = None
    subject: Optional[str] = None
    email_message_id: Optional[str] = None

    # --- chunking ---
    is_continuation: Optional[bool] = None
    orig_elements: Optional[list["Element"]] = None
    chunk_index: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in dataclasses.fields(self):
            v = getattr(self, f.name)
            if v is None:
                continue
            if f.name == "coordinates":
                v = v.to_dict()
            elif f.name == "orig_elements":
                v = [e.to_dict() for e in v]
            elif isinstance(v, list):
                v = list(v)
            out[f.name] = v
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ElementMetadata":
        known = {f.name for f in dataclasses.fields(cls)}
        kwargs = {k: v for k, v in d.items() if k in known}
        if kwargs.get("coordinates") is not None:
            kwargs["coordinates"] = Coordinates.from_dict(kwargs["coordinates"])
        if kwargs.get("orig_elements") is not None:
            kwargs["orig_elements"] = [element_from_dict(e) for e in kwargs["orig_elements"]]
        return cls(**kwargs)

    def copy(self) -> "ElementMetadata":
        return copy.deepcopy(self)


class Element:
    """Base class for all document elements."""

    category = "Element"

    def __init__(
        self,
        text: str = "",
        metadata: Optional[ElementMetadata] = None,
        element_id: Optional[str] = None,
    ):
        self.text = text
        self.metadata = metadata if metadata is not None else ElementMetadata()
        self._element_id = element_id

    # ------------------------------------------------------------------ identity
    @property
    def id(self) -> str:
        if self._element_id is None:
            self._element_id = uuid.uuid4().hex
        return self._element_id

    @id.setter
    def id(self, value: str) -> None:
        self._element_id = value

    def assign_hash_id(self, sequence_number: int) -> str:
        """Deterministic id: same file + same content + same position => same id.

        Stable ids let downstream tables (e.g. BigQuery) upsert re-parsed documents.
        """
        key = (
            f"{self.metadata.filename}|{self.category}|{self.text}|"
            f"{self.metadata.page_number}|{sequence_number}"
        )
        self._element_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self._element_id

    # ------------------------------------------------------------- conversion
    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.category,
            "element_id": self.id,
            "text": self.text,
            "metadata": self.metadata.to_dict(),
        }

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        snippet = self.text if len(self.text) <= 60 else self.text[:57] + "..."
        return f"<{self.category} {snippet!r}>"

    def __str__(self) -> str:
        return self.text

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Element)
            and self.category == other.category
            and self.text == other.text
            and self.metadata.to_dict() == other.metadata.to_dict()
        )

    def __hash__(self) -> int:
        return hash((self.category, self.text))


# --------------------------------------------------------------------- categories
class Text(Element):
    category = "Text"


class Title(Text):
    category = "Title"


class NarrativeText(Text):
    category = "NarrativeText"


class ListItem(Text):
    category = "ListItem"


class UncategorizedText(Text):
    category = "UncategorizedText"


class Table(Text):
    category = "Table"


class TableChunk(Table):
    category = "TableChunk"


class Header(Text):
    category = "Header"


class Footer(Text):
    category = "Footer"


class Address(Text):
    category = "Address"


class EmailAddress(Text):
    category = "EmailAddress"


class FigureCaption(Text):
    category = "FigureCaption"


class Image(Text):
    category = "Image"


class Formula(Text):
    category = "Formula"


class CodeSnippet(Text):
    category = "CodeSnippet"


class PageBreak(Element):
    category = "PageBreak"


class CompositeElement(Text):
    """A chunk: several consecutive elements merged into one text window."""

    category = "CompositeElement"


ELEMENT_CLASSES: dict[str, type[Element]] = {
    cls.category: cls
    for cls in (
        Text, Title, NarrativeText, ListItem, UncategorizedText, Table, TableChunk, Header,
        Footer, Address, EmailAddress, FigureCaption, Image, Formula, CodeSnippet, PageBreak,
        CompositeElement,
    )
}


def element_from_dict(d: dict[str, Any]) -> Element:
    cls = ELEMENT_CLASSES.get(d.get("type", "Text"), Text)
    return cls(
        text=d.get("text", ""),
        metadata=ElementMetadata.from_dict(d.get("metadata", {})),
        element_id=d.get("element_id"),
    )


def elements_to_dicts(elements: Iterable[Element]) -> list[dict[str, Any]]:
    return [e.to_dict() for e in elements]


def elements_from_dicts(dicts: Iterable[dict[str, Any]]) -> list[Element]:
    return [element_from_dict(d) for d in dicts]
