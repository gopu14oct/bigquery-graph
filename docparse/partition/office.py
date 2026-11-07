"""Office Open XML (DOCX, PPTX, XLSX) and EPUB partitioners.

All four formats are ZIP archives of XML parts. We read them directly with
``zipfile`` + ``xml.etree`` - no python-docx / python-pptx / openpyxl needed.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional

from docparse.cleaners import clean_extra_whitespace
from docparse.elements import (
    Element,
    ElementMetadata,
    Footer,
    Header,
    ListItem,
    PageBreak,
    Table,
    Text,
    Title,
)
from docparse.partition.common import FileLike, base_metadata, classify_text, finalize, read_bytes
from docparse.partition.html import html_to_elements, rows_to_html, rows_to_text

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dcterms": "http://purl.org/dc/terms/",
}
W = "{%s}" % NS["w"]
A = "{%s}" % NS["a"]
P = "{%s}" % NS["p"]
R = "{%s}" % NS["r"]


def _open_zip(filename, file) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(read_bytes(filename, file)))


def _xml(z: zipfile.ZipFile, name: str) -> Optional[ET.Element]:
    try:
        return ET.fromstring(z.read(name))
    except KeyError:
        return None


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Relationship id -> (target path resolved against ``part``, type)."""
    d, base = posixpath.split(part)
    root = _xml(z, posixpath.join(d, "_rels", base + ".rels"))
    out = {}
    if root is None:
        return out
    for rel in root:
        target = rel.get("Target", "")
        if rel.get("TargetMode") != "External":
            target = posixpath.normpath(posixpath.join(d, target)).lstrip("/")
        out[rel.get("Id")] = (target, rel.get("Type", "").rsplit("/", 1)[-1])
    return out


def _core_modified(z: zipfile.ZipFile) -> Optional[str]:
    root = _xml(z, "docProps/core.xml")
    if root is None:
        return None
    node = root.find("dcterms:modified", NS)
    return node.text if node is not None else None


# =================================================================== DOCX
class _DocxStyles:
    def __init__(self, z: zipfile.ZipFile):
        self.names: dict[str, str] = {}
        self.based_on: dict[str, str] = {}
        self.outline: dict[str, int] = {}
        self.list_level: dict[str, int] = {}
        root = _xml(z, "word/styles.xml")
        if root is None:
            return
        for st in root.iter(W + "style"):
            sid = st.get(W + "styleId")
            name = st.find(W + "name")
            self.names[sid] = name.get(W + "val") if name is not None else sid
            b = st.find(W + "basedOn")
            if b is not None:
                self.based_on[sid] = b.get(W + "val")
            if st.find(f"{W}pPr/{W}numPr") is not None:
                il = st.find(f"{W}pPr/{W}numPr/{W}ilvl")
                self.list_level[sid] = int(il.get(W + "val")) if il is not None else 0
            ol = st.find(f"{W}pPr/{W}outlineLvl")
            if ol is not None:
                self.outline[sid] = int(ol.get(W + "val"))

    def heading_level(self, sid: Optional[str]) -> Optional[int]:
        seen = set()
        while sid and sid not in seen:
            seen.add(sid)
            name = self.names.get(sid, sid).lower()
            if name == "title":
                return 0
            if name == "subtitle":
                return 1
            m = re.match(r"heading\s*(\d)", name)
            if m:
                return int(m.group(1)) - 1
            if sid in self.outline and self.outline[sid] < 9:
                return self.outline[sid]
            sid = self.based_on.get(sid)
        return None

    def is_list(self, sid: Optional[str]) -> bool:
        name = self.names.get(sid or "", "").lower()
        return ("list" in name and "table" not in name) or self.style_list_level(sid) is not None

    def style_list_level(self, sid: Optional[str]) -> Optional[int]:
        """List level defined by the style itself (e.g. "List Bullet 2" -> 1)."""
        seen = set()
        while sid and sid not in seen:
            seen.add(sid)
            if sid in self.list_level:
                lvl = self.list_level[sid]
                m = re.search(r"list (?:bullet|number|continue|paragraph)?\s*(\d)$", self.names.get(sid, "").lower())
                if lvl == 0 and m:
                    return int(m.group(1)) - 1
                return lvl
            m = re.search(r"^list (?:bullet|number|continue)\s*(\d)$", self.names.get(sid, "").lower())
            if m:
                return int(m.group(1)) - 1
            sid = self.based_on.get(sid)
        return None


class _DocxReader:
    def __init__(self, z: zipfile.ZipFile, base: ElementMetadata):
        self.z = z
        self.base = base
        self.styles = _DocxStyles(z)
        self.rels = _rels(z, "word/document.xml")
        self.page = 1
        self.elements: list[Element] = []

    def md(self, **kw) -> ElementMetadata:
        m = self.base.copy()
        m.page_number = self.page
        for k, v in kw.items():
            setattr(m, k, v)
        return m

    def paragraph(self, p: ET.Element) -> tuple[str, dict, bool]:
        """Return (text, inline metadata, page_break_after)."""
        parts: list[str] = []
        emph_text, emph_tags, link_text, link_url = [], [], [], []
        page_break = False

        def run_text(r: ET.Element) -> str:
            nonlocal page_break
            buf = []
            for c in r:
                if c.tag == W + "t":
                    buf.append(c.text or "")
                elif c.tag == W + "tab":
                    buf.append("\t")
                elif c.tag in (W + "br", W + "cr"):
                    if c.get(W + "type") == "page":
                        page_break = True
                    else:
                        buf.append("\n")
                elif c.tag == W + "lastRenderedPageBreak":
                    pass
            return "".join(buf)

        for child in p:
            if child.tag == W + "r":
                t = run_text(child)
                rpr = child.find(W + "rPr")
                if rpr is not None and t.strip():
                    for tag, short in (("b", "b"), ("i", "i"), ("u", "u")):
                        node = rpr.find(W + tag)
                        if node is not None and node.get(W + "val") not in ("0", "false"):
                            emph_text.append(t.strip()); emph_tags.append(short)
                parts.append(t)
            elif child.tag == W + "hyperlink":
                t = "".join(run_text(r) for r in child.iter(W + "r"))
                rid = child.get(R + "id")
                if rid and rid in self.rels:
                    link_text.append(t.strip()); link_url.append(self.rels[rid][0])
                parts.append(t)
            elif child.tag in (W + "ins", W + "smartTag", W + "sdt", W + "fldSimple"):
                parts.append("".join(run_text(r) for r in child.iter(W + "r")))
        meta = {}
        if emph_text:
            meta.update(emphasized_text_contents=emph_text, emphasized_text_tags=emph_tags)
        if link_url:
            meta.update(link_texts=link_text, link_urls=link_url)
        return "".join(parts), meta, page_break

    def table(self, tbl: ET.Element) -> None:
        rows = []
        for tr in tbl.findall(W + "tr"):
            cells = []
            for tc in tr.findall(W + "tc"):
                txt = " ".join(self.paragraph(p)[0] for p in tc.iter(W + "p"))
                span = tc.find(f"{W}tcPr/{W}gridSpan")
                n = int(span.get(W + "val")) if span is not None else 1
                cells.extend([clean_extra_whitespace(txt)] + [""] * (n - 1))
            rows.append(cells)
        if any(any(r) for r in rows):
            self.elements.append(Table(rows_to_text(rows), self.md(text_as_html=rows_to_html(rows))))

    def body(self) -> list[Element]:
        doc = _xml(self.z, "word/document.xml")
        body = doc.find(W + "body") if doc is not None else None
        if body is None:
            return []
        self._header_footer()
        for block in body:
            if block.tag == W + "p":
                self.block_paragraph(block)
            elif block.tag == W + "tbl":
                self.table(block)
            elif block.tag == W + "sdt":
                for p in block.iter(W + "p"):
                    self.block_paragraph(p)
        return self.elements

    def block_paragraph(self, p: ET.Element) -> None:
        text, meta, page_break = self.paragraph(p)
        ppr = p.find(W + "pPr")
        style = None
        num_level = None
        if ppr is not None:
            ps = ppr.find(W + "pStyle")
            style = ps.get(W + "val") if ps is not None else None
            ilvl = ppr.find(f"{W}numPr/{W}ilvl")
            if ppr.find(W + "numPr") is not None:
                num_level = int(ilvl.get(W + "val")) if ilvl is not None else 0
            if ppr.find(W + "pageBreakBefore") is not None and self.elements:
                self._page_break()
        text = clean_extra_whitespace(text)
        if text:
            style_name = self.styles.names.get(style, style) if style else None
            level = self.styles.heading_level(style)
            md = self.md(style_name=style_name, **meta)
            if level is not None:
                md.category_depth = level
                self.elements.append(Title(text, md))
            elif num_level is not None or self.styles.is_list(style):
                md.category_depth = num_level if num_level is not None else (self.styles.style_list_level(style) or 0)
                self.elements.append(ListItem(text, md))
            else:
                el = classify_text(text, md)
                if el is not None:
                    self.elements.append(el)
        if page_break:
            self._page_break()

    def _page_break(self) -> None:
        self.elements.append(PageBreak("", self.md()))
        self.page += 1

    def _header_footer(self) -> None:
        doc_rels = self.rels
        seen = set()
        for target, rtype in doc_rels.values():
            if rtype not in ("header", "footer") or target in seen:
                continue
            seen.add(target)
            root = _xml(self.z, target)
            if root is None:
                continue
            txt = clean_extra_whitespace(" ".join("".join(t.text or "" for t in p.iter(W + "t")) for p in root.iter(W + "p")))
            if txt:
                cls = Header if rtype == "header" else Footer
                self.elements.append(cls(txt, self.md(header_footer_type="primary")))


def partition_docx(
    filename: Optional[str] = None,
    file: Optional[FileLike] = None,
    metadata_filename: Optional[str] = None,
    languages: Optional[list[str]] = None,
    include_page_breaks: bool = True,
    unique_element_ids: bool = False,
    **_: object,
) -> list[Element]:
    z = _open_zip(filename, file)
    base = base_metadata(filename, "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         metadata_filename, last_modified=_core_modified(z))
    elements = _DocxReader(z, base).body()
    return finalize(elements, base, languages, include_page_breaks, unique_element_ids)


# =================================================================== PPTX
def _shape_sort_key(sp: ET.Element) -> tuple[int, int]:
    off = sp.find(".//" + A + "off")
    if off is None:
        return (10**9, 10**9)
    return (int(off.get("y", 0)), int(off.get("x", 0)))


def partition_pptx(
    filename: Optional[str] = None,
    file: Optional[FileLike] = None,
    metadata_filename: Optional[str] = None,
    languages: Optional[list[str]] = None,
    include_page_breaks: bool = True,
    include_slide_notes: bool = True,
    unique_element_ids: bool = False,
    **_: object,
) -> list[Element]:
    z = _open_zip(filename, file)
    base = base_metadata(filename, "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                         metadata_filename, last_modified=_core_modified(z))
    pres = _xml(z, "ppt/presentation.xml")
    rels = _rels(z, "ppt/presentation.xml")
    slide_parts = []
    if pres is not None:
        for sid in pres.iter(P + "sldId"):
            target = rels.get(sid.get(R + "id"), (None, None))[0]
            if target:
                slide_parts.append(target)
    elements: list[Element] = []
    for page, part in enumerate(slide_parts, start=1):
        root = _xml(z, part)
        if root is None:
            continue

        def md(**kw) -> ElementMetadata:
            m = base.copy(); m.page_number = page
            for k, v in kw.items():
                setattr(m, k, v)
            return m

        shapes = [s for s in root.iter() if s.tag in (P + "sp", P + "graphicFrame")]
        for sp in sorted(shapes, key=_shape_sort_key):
            if sp.tag == P + "graphicFrame":
                tbl = sp.find(".//" + A + "tbl")
                if tbl is None:
                    continue
                rows = [[clean_extra_whitespace("".join(t.text or "" for t in tc.iter(A + "t"))) for tc in tr.findall(A + "tc")]
                        for tr in tbl.findall(A + "tr")]
                if any(any(r) for r in rows):
                    elements.append(Table(rows_to_text(rows), md(text_as_html=rows_to_html(rows))))
                continue
            ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
            ph_type = ph.get("type", "body") if ph is not None else None
            if ph_type in ("dt", "sldNum", "ftr"):
                txt = clean_extra_whitespace(" ".join(t.text or "" for t in sp.iter(A + "t")))
                if txt and ph_type == "ftr":
                    elements.append(Footer(txt, md()))
                continue
            body = sp.find(P + "txBody")
            if body is None:
                continue
            is_title = ph_type in ("title", "ctrTitle")
            if is_title:
                txt = clean_extra_whitespace(" ".join("".join(t.text or "" for t in para.iter(A + "t")) for para in body.findall(A + "p")))
                if txt:
                    elements.append(Title(txt, md(category_depth=0)))
                continue
            for para in body.findall(A + "p"):
                txt = clean_extra_whitespace("".join(t.text or "" for t in para.iter(A + "t")))
                if not txt:
                    continue
                ppr = para.find(A + "pPr")
                lvl = int(ppr.get("lvl", 0)) if ppr is not None else 0
                bulleted = ppr is not None and (ppr.find(A + "buChar") is not None or ppr.find(A + "buAutoNum") is not None)
                no_bullet = ppr is not None and ppr.find(A + "buNone") is not None
                if ph_type == "subTitle":
                    elements.append(Title(txt, md(category_depth=1)))
                elif bulleted or (ph_type == "body" and not no_bullet) or lvl > 0:
                    elements.append(ListItem(txt, md(category_depth=lvl)))
                else:
                    el = classify_text(txt, md())
                    if el is not None:
                        elements.append(el)
        if include_slide_notes:
            for target, rtype in _rels(z, part).values():
                if rtype == "notesSlide":
                    nroot = _xml(z, target)
                    if nroot is None:
                        continue
                    for sp in nroot.iter(P + "sp"):
                        ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                        if ph is not None and ph.get("type") == "body":
                            txt = clean_extra_whitespace(" ".join(t.text or "" for t in sp.iter(A + "t")))
                            if txt:
                                elements.append(Text(txt, md(style_name="speaker_notes")))
        if page < len(slide_parts):
            elements.append(PageBreak("", md()))
    return finalize(elements, base, languages, include_page_breaks, unique_element_ids)


# =================================================================== XLSX
def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def partition_xlsx(
    filename: Optional[str] = None,
    file: Optional[FileLike] = None,
    metadata_filename: Optional[str] = None,
    languages: Optional[list[str]] = None,
    include_header: bool = True,
    unique_element_ids: bool = False,
    **_: object,
) -> list[Element]:
    """One ``Table`` per worksheet; ``page_number`` is the sheet index, ``page_name`` its name."""
    S = "{%s}" % NS["s"]
    z = _open_zip(filename, file)
    base = base_metadata(filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         metadata_filename, last_modified=_core_modified(z))
    shared: list[str] = []
    sst = _xml(z, "xl/sharedStrings.xml")
    if sst is not None:
        for si in sst.findall(S + "si"):
            shared.append("".join(t.text or "" for t in si.iter(S + "t")))
    wb = _xml(z, "xl/workbook.xml")
    rels = _rels(z, "xl/workbook.xml")
    elements: list[Element] = []
    sheets = wb.find(S + "sheets") if wb is not None else None
    for idx, sheet in enumerate(list(sheets) if sheets is not None else [], start=1):
        part = rels.get(sheet.get(R + "id"), (None,))[0]
        root = _xml(z, part) if part else None
        if root is None:
            continue
        grid: dict[int, dict[int, str]] = {}
        for row in root.iter(S + "row"):
            r = int(row.get("r", len(grid) + 1))
            for c in row.findall(S + "c"):
                ctype = c.get("t")
                col = _col_index(c.get("r")) if c.get("r") else len(grid.get(r, {}))
                v = c.find(S + "v")
                if ctype == "s" and v is not None:
                    val = shared[int(v.text)]
                elif ctype == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(S + "t"))
                elif ctype == "b" and v is not None:
                    val = "TRUE" if v.text == "1" else "FALSE"
                else:
                    val = v.text if v is not None and v.text is not None else ""
                    if re.fullmatch(r"-?\d+\.0", val):
                        val = val[:-2]
                if val != "":
                    grid.setdefault(r, {})[col] = val.strip()
        if not grid:
            continue
        ncols = max(max(cols) for cols in grid.values()) + 1
        rows = [[grid[r].get(c, "") for c in range(ncols)] for r in sorted(grid)]
        if not include_header and len(rows) > 1:
            rows = rows[1:]
        md = base.copy()
        md.page_number = idx
        md.page_name = sheet.get("name")
        md.text_as_html = rows_to_html(rows)
        elements.append(Table(rows_to_text(rows), md))
    return finalize(elements, base, languages, unique_element_ids=unique_element_ids)


# =================================================================== EPUB
def partition_epub(
    filename: Optional[str] = None,
    file: Optional[FileLike] = None,
    metadata_filename: Optional[str] = None,
    languages: Optional[list[str]] = None,
    unique_element_ids: bool = False,
    **_: object,
) -> list[Element]:
    """Walk the OPF spine in reading order and partition each XHTML chapter as HTML.
    ``page_number`` is the 1-based chapter (spine item) index."""
    z = _open_zip(filename, file)
    base = base_metadata(filename, "application/epub+zip", metadata_filename)
    container = _xml(z, "META-INF/container.xml")
    opf_path = None
    if container is not None:
        for rf in container.iter():
            if rf.tag.endswith("rootfile"):
                opf_path = rf.get("full-path"); break
    if opf_path is None:
        raise ValueError("Not a valid EPUB: missing META-INF/container.xml rootfile")
    opf = _xml(z, opf_path)
    opf_dir = posixpath.dirname(opf_path)
    manifest = {it.get("id"): it.get("href") for it in opf.iter() if it.tag.endswith("item")}
    spine = [ir.get("idref") for ir in opf.iter() if ir.tag.endswith("itemref")]
    lang_node = next((n for n in opf.iter() if n.tag.endswith("}language")), None)
    elements: list[Element] = []
    for chapter, idref in enumerate(spine, start=1):
        href = manifest.get(idref)
        if not href:
            continue
        try:
            markup = z.read(posixpath.normpath(posixpath.join(opf_dir, href))).decode("utf-8", errors="replace")
        except KeyError:
            continue
        elements.extend(html_to_elements(markup, base.copy(), page_number=chapter))
    langs = languages or ([lang_node.text] if lang_node is not None and lang_node.text else None)
    return finalize(elements, base, langs, unique_element_ids=unique_element_ids)
