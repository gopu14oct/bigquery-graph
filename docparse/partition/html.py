"""HTML partitioner built on the stdlib tokenizer (``html.parser``).

We build a tiny DOM ourselves, then walk it, emitting one element per *block*:
headings -> Title (depth = level-1), ``<li>`` -> ListItem (depth = list nesting),
``<table>`` -> Table (with normalised ``text_as_html``), ``<pre>`` -> CodeSnippet,
everything else block-level -> classified by the text heuristics. Inline markup
(``<b>``, ``<a>``...) is folded into its block and recorded as emphasis/link metadata.
"""

from __future__ import annotations

import html as _html
from html.parser import HTMLParser
from typing import Optional

from docparse.cleaners import clean_extra_whitespace
from docparse.elements import (
    CodeSnippet,
    Element,
    ElementMetadata,
    FigureCaption,
    Footer,
    Header,
    Image,
    ListItem,
    PageBreak,
    Table,
    Text,
    Title,
)
from docparse.partition.common import FileLike, base_metadata, classify_text, decode_text, finalize, read_bytes

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SKIP_TAGS = {"script", "style", "noscript", "template", "head", "title", "svg", "iframe", "object", "button", "select", "form"}
HEADING_TAGS = {"h1": 0, "h2": 1, "h3": 2, "h4": 3, "h5": 4, "h6": 5}
BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "aside", "blockquote", "address", "dd", "dt", "dl",
    "figure", "figcaption", "header", "footer", "nav", "center", "body", "html", "details", "summary",
    "fieldset", "td", "th", "caption", "label",
} | set(HEADING_TAGS) | {"ul", "ol", "li", "table", "pre", "hr"}
EMPHASIS_TAGS = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u"}
# Tags whose end is implied by the start of another block (HTML5 optional end tags).
AUTO_CLOSE = {"p": BLOCK_TAGS, "li": {"li"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"},
              "tr": {"tr"}, "td": {"td", "th", "tr"}, "th": {"td", "th", "tr"}, "option": {"option"}}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: Optional[dict] = None, parent: Optional["Node"] = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list = []  # Node | str
        self.parent = parent

    def text(self) -> str:
        parts: list[str] = []
        for c in self.children:
            if isinstance(c, str):
                parts.append(c)
            elif c.tag == "br":
                parts.append("\n")
            elif c.tag not in SKIP_TAGS:
                parts.append(c.text())
                if c.tag in BLOCK_TAGS:
                    parts.append(" ")
        return "".join(parts)

    def iter(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.iter()


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        # Implicitly close elements like <p> / <li> that HTML lets authors leave open.
        node = self.cur
        while node is not self.root and node.tag in AUTO_CLOSE and tag in AUTO_CLOSE[node.tag]:
            if tag in ("td", "th") and node.tag not in ("td", "th"):
                break
            self.cur = node.parent
            node = self.cur
        el = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(el)
        if tag not in VOID_TAGS:
            self.cur = el

    def handle_startendtag(self, tag, attrs):
        el = Node(tag.lower(), {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(el)

    def handle_endtag(self, tag):
        tag = tag.lower()
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse_html(markup: str) -> Node:
    b = _TreeBuilder()
    b.feed(markup)
    b.close()
    return b.root


# ------------------------------------------------------------------- tables
def table_rows(table: Node) -> list[list[str]]:
    rows: list[list[str]] = []
    for node in table.iter():
        if node.tag == "tr":
            # Skip rows that belong to a nested table.
            anc = node.parent
            while anc is not None and anc.tag != "table":
                anc = anc.parent
            if anc is not table:
                continue
            cells = []
            for c in node.children:
                if isinstance(c, Node) and c.tag in ("td", "th"):
                    txt = clean_extra_whitespace(c.text())
                    span = int(c.attrs.get("colspan", "1") or 1) if str(c.attrs.get("colspan", "1")).isdigit() else 1
                    cells.extend([txt] + [""] * (span - 1))
            if cells:
                rows.append(cells)
    return rows


def rows_to_html(rows: list[list[str]]) -> str:
    body = "".join("<tr>" + "".join(f"<td>{_html.escape(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table>{body}</table>"


def rows_to_text(rows: list[list[str]]) -> str:
    return "\n".join(" ".join(c for c in r if c) for r in rows if any(r))


# ------------------------------------------------------------------- walk
class _Walker:
    def __init__(self, base_md: ElementMetadata, page_number: Optional[int], has_headings: bool = False):
        self.base_md = base_md
        # When the author used real <h1>-<h6> headings, trust them: a plain <p> is only
        # promoted to Title by the text heuristics if it is entirely bold/emphasised.
        self.has_headings = has_headings
        self.elements: list[Element] = []
        self.page = page_number

    def md(self, **kw) -> ElementMetadata:
        m = self.base_md.copy()
        m.page_number = self.page
        for k, v in kw.items():
            setattr(m, k, v)
        return m

    @staticmethod
    def inline_meta(node: Node) -> dict:
        emph_text, emph_tags, link_text, link_url = [], [], [], []
        for n in node.iter():
            if n.tag in EMPHASIS_TAGS:
                t = clean_extra_whitespace(n.text())
                if t:
                    emph_text.append(t)
                    emph_tags.append(EMPHASIS_TAGS[n.tag])
            elif n.tag == "a" and n.attrs.get("href"):
                link_text.append(clean_extra_whitespace(n.text()))
                link_url.append(n.attrs["href"])
        out = {}
        if emph_text:
            out.update(emphasized_text_contents=emph_text, emphasized_text_tags=emph_tags)
        if link_url:
            out.update(link_texts=link_text, link_urls=link_url)
        return out

    def emit_text_block(self, node: Node, text: str, hint: Optional[str] = None) -> None:
        text = clean_extra_whitespace(text)
        if not text:
            return
        md = self.md(**self.inline_meta(node))
        if hint == "header":
            self.elements.append(Header(text, md))
        elif hint == "footer":
            self.elements.append(Footer(text, md))
        elif node.tag == "figcaption" or node.tag == "caption":
            self.elements.append(FigureCaption(text, md))
        else:
            el = classify_text(text, md)
            if el is None:
                return
            if isinstance(el, Title) and self.has_headings:
                emph = " ".join(md.emphasized_text_contents or [])
                if clean_extra_whitespace(emph) != text:
                    el = Text(text, md)
            self.elements.append(el)

    def walk(self, node: Node, list_depth: int = -1, hint: Optional[str] = None) -> None:
        inline_buf: list = []  # inline children accumulated into an anonymous paragraph

        def flush():
            if not inline_buf:
                return
            tmp = Node("p")
            tmp.children = list(inline_buf)
            self.emit_text_block(tmp, tmp.text(), hint)
            inline_buf.clear()

        for child in node.children:
            if isinstance(child, str):
                inline_buf.append(child)
                continue
            tag = child.tag
            if tag in SKIP_TAGS:
                continue
            if tag == "br":
                inline_buf.append(child)
                continue
            if tag == "img":
                flush()
                alt = child.attrs.get("alt", "").strip()
                if alt:
                    self.elements.append(Image(alt, self.md(url=child.attrs.get("src") or None)))
                continue
            if tag not in BLOCK_TAGS:
                if any(isinstance(d, Node) and d.tag in BLOCK_TAGS for d in child.iter() if d is not child):
                    flush()
                    self.walk(child, list_depth, hint)  # e.g. <span><p>..</p></span>
                else:
                    inline_buf.append(child)
                continue
            flush()
            if child.attrs.get("style", "").replace(" ", "").find("page-break-before:always") >= 0:
                self.page = (self.page or 1) + 1
                self.elements.append(PageBreak("", self.md()))
            if tag in HEADING_TAGS:
                text = clean_extra_whitespace(child.text())
                if text:
                    self.elements.append(Title(text, self.md(category_depth=HEADING_TAGS[tag], **self.inline_meta(child))))
            elif tag in ("ul", "ol"):
                self.walk(child, list_depth + 1, hint)
            elif tag == "li":
                own = Node("li")
                own.children = [c for c in child.children if not (isinstance(c, Node) and c.tag in ("ul", "ol"))]
                text = clean_extra_whitespace(own.text())
                if text:
                    self.elements.append(ListItem(text, self.md(category_depth=max(list_depth, 0), **self.inline_meta(own))))
                for c in child.children:
                    if isinstance(c, Node) and c.tag in ("ul", "ol"):
                        self.walk(c, list_depth + 1, hint)
            elif tag == "table":
                rows = table_rows(child)
                if rows:
                    self.elements.append(Table(rows_to_text(rows), self.md(text_as_html=rows_to_html(rows))))
                for cap in child.children:
                    if isinstance(cap, Node) and cap.tag == "caption":
                        self.emit_text_block(cap, cap.text())
            elif tag == "pre":
                code = child.text().strip("\n")
                if code.strip():
                    self.elements.append(CodeSnippet(code, self.md()))
            elif tag == "hr":
                continue
            elif tag in ("header", "footer") and hint is None and child.parent is not None and child.parent.tag in ("body", "#root", "html"):
                self.walk(child, list_depth, tag)
            else:
                # Container or paragraph-like block: recurse if it has nested blocks.
                if any(isinstance(c, Node) and c.tag in BLOCK_TAGS for c in child.children):
                    self.walk(child, list_depth, hint)
                else:
                    self.emit_text_block(child, child.text(), hint)
        flush()


def html_to_elements(markup: str, base_md: Optional[ElementMetadata] = None, page_number: Optional[int] = None) -> list[Element]:
    root = parse_html(markup)
    body = next((n for n in root.iter() if n.tag == "body"), root)
    has_headings = any(n.tag in HEADING_TAGS for n in body.iter())
    w = _Walker(base_md or ElementMetadata(), page_number, has_headings)
    w.walk(body)
    return w.elements


def partition_html(
    filename: Optional[str] = None,
    file: Optional[FileLike] = None,
    text: Optional[str] = None,
    encoding: Optional[str] = None,
    metadata_filename: Optional[str] = None,
    languages: Optional[list[str]] = None,
    include_page_breaks: bool = False,
    unique_element_ids: bool = False,
    url: Optional[str] = None,
    **_: object,
) -> list[Element]:
    if text is None:
        text = decode_text(read_bytes(filename, file), encoding)
    base = base_metadata(filename, "text/html", metadata_filename)
    base.url = url
    lang = None
    root_html = parse_html(text[:4096])
    for n in root_html.iter():
        if n.tag == "html" and n.attrs.get("lang"):
            lang = [n.attrs["lang"]]
            break
    elements = html_to_elements(text, page_number=None)
    return finalize(elements, base, languages or lang, include_page_breaks, unique_element_ids)
