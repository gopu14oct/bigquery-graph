"""Generate a benchmark corpus with exact ground truth.

Each document is generated from a random *spec* (a list of blocks: Title(depth),
Paragraph, ListItem(depth), Table(rows)), then rendered into one or more formats:

* docx      - python-docx with real Heading / List Bullet styles
* html      - semantic HTML with <header>/<footer> furniture
* md        - CommonMark + GFM tables
* pdf       - the docx rendered by LibreOffice (real fonts, running header/footer)
* pdf2col   - reportlab platypus, two-column layout, running header/footer, ruled tables
* scanned   - the LibreOffice pdf rasterised at 150 dpi (image-only, needs OCR)

Text is sampled from real English sentences (stdlib docstrings), not lorem ipsum,
so classifiers see realistic prose.

    python benchmarks/generate.py --out benchmarks/data --split dev  --n 12
    python benchmarks/generate.py --out benchmarks/data --split test --n 12
"""

from __future__ import annotations

import argparse
import html
import inspect
import json
import os
import random
import re
import shutil
import subprocess
import tempfile

SPLIT_SEEDS = {"dev": 1000, "test": 9000}  # disjoint seed ranges


# ----------------------------------------------------------------- corpus
def build_corpus() -> tuple[list[str], list[str]]:
    import collections, email, http.client, json as _j, logging, pathlib, shutil as _s, statistics, string, textwrap, threading, unittest, urllib.request, zipfile  # noqa: E401,F401
    mods = [collections, email, http.client, _j, logging, pathlib, _s, statistics, string, textwrap, threading, unittest, urllib.request, zipfile]
    sentences, words = set(), collections.Counter()
    for m in mods:
        for _, obj in inspect.getmembers(m):
            doc = inspect.getdoc(obj) or ""
            for para in doc.split("\n\n"):
                para = " ".join(para.split())
                if not para or any(c in para for c in "<>|`*_=#{}[]\\") or ">>>" in para:
                    continue
                for s in re.split(r"(?<=[.!?])\s+", para):
                    if 6 <= len(s.split()) <= 28 and s[0].isupper() and s.endswith(".") and not re.search(r"\w\(|\.\w", s[:-1]):
                        sentences.add(s)
                        words.update(w.lower() for w in re.findall(r"\b[A-Za-z]{4,12}\b", s))
    vocab = [w for w, c in words.most_common(1500) if c >= 2]
    return sorted(sentences), vocab


# ----------------------------------------------------------------- spec
def make_spec(rng: random.Random, sentences, vocab) -> dict:
    def heading():
        return " ".join(w.capitalize() for w in rng.sample(vocab, rng.randint(2, 5)))

    def paragraph():
        return " ".join(rng.sample(sentences, rng.randint(2, 5)))

    def item():
        s = rng.choice(sentences).rstrip(".")
        words = s.split()
        return " ".join(words[: rng.randint(3, min(12, len(words)))])

    def table():
        ncols, nrows = rng.randint(2, 5), rng.randint(3, 7)
        header = [w.capitalize() for w in rng.sample(vocab, ncols)]
        rows = [header]
        for _ in range(nrows - 1):
            rows.append([rng.choice([rng.choice(vocab), str(rng.randint(0, 9999)), f"{rng.uniform(0, 100):.1f}"]) for _ in range(ncols)])
        return rows

    blocks = [{"type": "Title", "depth": 0, "text": heading()}]
    for _ in range(rng.randint(3, 5)):  # sections
        blocks.append({"type": "Title", "depth": 1, "text": heading()})
        for _ in range(rng.randint(1, 3)):
            blocks.append({"type": "Paragraph", "text": paragraph()})
        if rng.random() < 0.6:
            blocks.append({"type": "Title", "depth": 2, "text": heading()})
            blocks.append({"type": "Paragraph", "text": paragraph()})
        if rng.random() < 0.6:
            for _ in range(rng.randint(2, 4)):
                blocks.append({"type": "ListItem", "depth": 0, "text": item()})
                if rng.random() < 0.3:
                    blocks.append({"type": "ListItem", "depth": 1, "text": item()})
        if rng.random() < 0.45:
            blocks.append({"type": "Table", "rows": table()})
        if rng.random() < 0.5:
            blocks.append({"type": "Paragraph", "text": paragraph()})
    # dedupe accidental identical texts so matching is unambiguous
    seen = set()
    for b in blocks:
        key = b.get("text") or json.dumps(b.get("rows"))
        while key in seen:
            b["text"] = b["text"] + " " + rng.choice(vocab)
            key = b["text"]
        seen.add(key)
    return {
        "blocks": blocks,
        "header": f"{heading()} Report",
        "footer": "Confidential",
    }


# ----------------------------------------------------------------- renderers
def render_docx(spec, path):
    import docx

    d = docx.Document()
    sec = d.sections[0]
    sec.header.paragraphs[0].text = spec["header"]
    sec.footer.paragraphs[0].text = spec["footer"]
    for b in spec["blocks"]:
        if b["type"] == "Title":
            d.add_heading(b["text"], level=b["depth"] + 1)
        elif b["type"] == "Paragraph":
            d.add_paragraph(b["text"])
        elif b["type"] == "ListItem":
            d.add_paragraph(b["text"], style="List Bullet" if b["depth"] == 0 else "List Bullet 2")
        elif b["type"] == "Table":
            rows = b["rows"]
            t = d.add_table(rows=len(rows), cols=len(rows[0]))
            t.style = "Table Grid"
            for i, r in enumerate(rows):
                for j, v in enumerate(r):
                    t.cell(i, j).text = v
    d.save(path)


def render_html(spec, path):
    out = ["<!doctype html><html lang='en'><head><meta charset='utf-8'><title>doc</title></head><body>",
           f"<header><p>{html.escape(spec['header'])}</p></header><main>"]
    depth_open = -1
    for b in spec["blocks"]:
        if b["type"] != "ListItem":
            while depth_open >= 0:
                out.append("</li></ul>"); depth_open -= 1
        if b["type"] == "Title":
            out.append(f"<h{b['depth'] + 1}>{html.escape(b['text'])}</h{b['depth'] + 1}>")
        elif b["type"] == "Paragraph":
            out.append(f"<p>{html.escape(b['text'])}</p>")
        elif b["type"] == "ListItem":
            d = b["depth"]
            if d > depth_open:
                out.append("<ul><li>"); depth_open += 1
            else:
                while depth_open > d:
                    out.append("</li></ul>"); depth_open -= 1
                out.append("</li><li>")
            out.append(html.escape(b["text"]))
        elif b["type"] == "Table":
            rows = b["rows"]
            out.append("<table><thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in rows[0]) + "</tr></thead><tbody>")
            for r in rows[1:]:
                out.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>")
            out.append("</tbody></table>")
    while depth_open >= 0:
        out.append("</li></ul>"); depth_open -= 1
    out.append(f"</main><footer><p>{html.escape(spec['footer'])}</p></footer></body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def render_md(spec, path):
    out = []
    for b in spec["blocks"]:
        if b["type"] == "Title":
            out.append("#" * (b["depth"] + 1) + " " + b["text"] + "\n")
        elif b["type"] == "Paragraph":
            out.append(b["text"] + "\n")
        elif b["type"] == "ListItem":
            out.append("  " * b["depth"] + "- " + b["text"])
        elif b["type"] == "Table":
            rows = b["rows"]
            out.append("")
            out.append("| " + " | ".join(rows[0]) + " |")
            out.append("|" + "---|" * len(rows[0]))
            for r in rows[1:]:
                out.append("| " + " | ".join(r) + " |")
            out.append("")
    text = "\n".join(out)
    text = re.sub(r"(\n- [^\n]+|\n  - [^\n]+)\n(?=[^-\s|#])", r"\1\n\n", text)  # blank line after lists
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")


def render_pdf2col(spec, path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle

    ss = getSampleStyleSheet()
    W, H = letter
    margin, gutter = 54, 18
    colw = (W - 2 * margin - gutter) / 2

    def furniture(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(margin, H - 36, spec["header"])
        canvas.drawRightString(W - margin, 30, f"{spec['footer']} - {doc.page}")
        canvas.restoreState()

    frames = [Frame(margin, margin, colw, H - 2 * margin - 10, id="c1"),
              Frame(margin + colw + gutter, margin, colw, H - 2 * margin - 10, id="c2")]
    doc = BaseDocTemplate(path, pagesize=letter, pageTemplates=[PageTemplate(frames=frames, onPage=furniture)])
    story = []
    styles = {0: ss["Heading1"], 1: ss["Heading2"], 2: ss["Heading3"]}
    from reportlab.lib.styles import ParagraphStyle

    item_styles = {
        0: ParagraphStyle("li0", parent=ss["BodyText"], leftIndent=18, bulletIndent=6),
        1: ParagraphStyle("li1", parent=ss["BodyText"], leftIndent=32, bulletIndent=20),
    }
    pending_items: list = []

    def flush_list():
        for depth, text in pending_items:
            story.append(Paragraph(html.escape(text), item_styles[depth], bulletText="\u2022" if depth == 0 else "\u2013"))
        pending_items.clear()

    for b in spec["blocks"]:
        if b["type"] != "ListItem":
            flush_list()
        if b["type"] == "Title":
            story.append(Paragraph(html.escape(b["text"]), styles[b["depth"]]))
        elif b["type"] == "Paragraph":
            story.append(Paragraph(html.escape(b["text"]), ss["BodyText"]))
        elif b["type"] == "ListItem":
            pending_items.append((b["depth"], b["text"]))
        elif b["type"] == "Table":
            t = Table(b["rows"], colWidths=[colw / len(b["rows"][0])] * len(b["rows"][0]))
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("FONTSIZE", (0, 0), (-1, -1), 7)]))
            story += [Spacer(1, 4), t, Spacer(1, 4)]
    flush_list()
    doc.build(story)


def to_pdf_with_libreoffice(docx_paths, outdir):
    if not docx_paths:
        return
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", "pdf",
                        "--outdir", outdir, *docx_paths], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)


def rasterize(pdf_path, out_path, dpi=150):
    from PIL import Image

    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", pdf_path, os.path.join(td, "p")], check=True)
        pages = [Image.open(os.path.join(td, f)).convert("RGB") for f in sorted(os.listdir(td))]
        pages[0].save(out_path, save_all=True, append_images=pages[1:], resolution=dpi)


# ----------------------------------------------------------------- main
FORMATS = ["docx", "html", "md", "pdf", "pdf2col", "scanned"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="benchmarks/data")
    ap.add_argument("--split", choices=list(SPLIT_SEEDS), required=True)
    ap.add_argument("--n", type=int, default=12)
    a = ap.parse_args()
    sentences, vocab = build_corpus()
    root = os.path.join(a.out, a.split)
    shutil.rmtree(root, ignore_errors=True)
    for fmt in FORMATS:
        os.makedirs(os.path.join(root, fmt), exist_ok=True)
    tmp_docx = []
    for i in range(a.n):
        seed = SPLIT_SEEDS[a.split] + i
        rng = random.Random(seed)
        spec = make_spec(rng, sentences, vocab)
        name = f"doc{i:03d}"
        with open(os.path.join(root, f"{name}.truth.json"), "w") as f:
            json.dump(spec, f, indent=1)
        render_docx(spec, os.path.join(root, "docx", name + ".docx"))
        render_html(spec, os.path.join(root, "html", name + ".html"))
        render_md(spec, os.path.join(root, "md", name + ".md"))
        render_pdf2col(spec, os.path.join(root, "pdf2col", name + ".pdf"))
        tmp_docx.append(os.path.join(root, "docx", name + ".docx"))
    to_pdf_with_libreoffice(tmp_docx, os.path.join(root, "pdf"))
    for i in range(a.n):
        name = f"doc{i:03d}"
        rasterize(os.path.join(root, "pdf", name + ".pdf"), os.path.join(root, "scanned", name + ".pdf"))
    print(f"wrote {a.n} docs x {len(FORMATS)} formats to {root} ({len(sentences)} corpus sentences)")


if __name__ == "__main__":
    main()
