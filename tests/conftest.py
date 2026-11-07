"""Fixtures that generate real sample documents on the fly (no binary files in git)."""

import zipfile
from email.message import EmailMessage

import pytest

LOREM = ("The quarterly results show that revenue increased across every region. "
         "Management expects this trend to continue as new products are launched. ")


@pytest.fixture
def docx_file(tmp_path):
    docx = pytest.importorskip("docx")
    from docx.enum.text import WD_BREAK

    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = "ACME Corp Confidential"
    d.add_heading("Annual Report", level=1)
    d.add_paragraph(LOREM)
    d.add_heading("Revenue", level=2)
    d.add_paragraph("North America grew fastest", style="List Bullet")
    d.add_paragraph("Europe was flat", style="List Bullet")
    p = d.add_paragraph()
    r = p.add_run("Bold statement here")
    r.bold = True
    p.add_run(" followed by normal text that forms a sentence.")
    t = d.add_table(rows=2, cols=3)
    for i, row in enumerate([["Region", "Q1", "Q2"], ["EMEA", "10", "20"]]):
        for j, v in enumerate(row):
            t.cell(i, j).text = v
    d.add_paragraph("End of page one.").runs[0].add_break(WD_BREAK.PAGE)
    d.add_heading("Outlook", level=1)
    d.add_paragraph(LOREM)
    path = tmp_path / "report.docx"
    d.save(path)
    return str(path)


@pytest.fixture
def pptx_file(tmp_path):
    pptx = pytest.importorskip("pptx")
    from pptx.util import Inches

    prs = pptx.Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Product Launch"
    s.placeholders[1].text = "Q3 2026 kickoff"
    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "Agenda"
    tf = s2.placeholders[1].text_frame
    tf.text = "Market overview"
    p = tf.add_paragraph(); p.text = "Competitors"; p.level = 1
    p = tf.add_paragraph(); p.text = "Pricing"
    s2.notes_slide.notes_text_frame.text = "Remember to mention the beta."
    s3 = prs.slides.add_slide(prs.slide_layouts[5])
    s3.shapes.title.text = "Numbers"
    tbl = s3.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    for i, row in enumerate([["Metric", "Value"], ["Users", "1200"]]):
        for j, v in enumerate(row):
            tbl.cell(i, j).text = v
    path = tmp_path / "deck.pptx"
    prs.save(path)
    return str(path)


@pytest.fixture
def xlsx_file(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Region", "Units", "Price"])
    ws.append(["EMEA", 10, 2.5])
    ws.append(["APAC", 7, 3])
    ws2 = wb.create_sheet("Notes")
    ws2["B2"] = "only cell"
    path = tmp_path / "book.xlsx"
    wb.save(path)
    return str(path)


@pytest.fixture
def pdf_file(tmp_path):
    pytest.importorskip("pdfplumber")
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    path = tmp_path / "paper.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    W, H = letter
    for page in (1, 2):
        c.setFont("Helvetica", 8)
        c.drawString(72, H - 30, "Journal of Examples")
        c.drawString(W / 2, 30, f"{page}")
        y = H - 90
        if page == 1:
            c.setFont("Helvetica-Bold", 20)
            c.drawString(72, y, "A Study of Parsing")
            y -= 40
            c.setFont("Helvetica-Bold", 14)
            c.drawString(72, y, "Introduction")
            y -= 24
        c.setFont("Helvetica", 10)
        # two columns
        for col_x, label in ((72, "Left"), (W / 2 + 20, "Right")):
            yy = y
            for i in range(6):
                c.drawString(col_x, yy, f"{label} column line {i} has some words in it")
                yy -= 13
        y -= 110
        if page == 1:
            # ruled table
            data = [["Name", "Score"], ["alpha", "1"], ["beta", "2"]]
            x0, cw, rh = 72, 100, 18
            for r, row in enumerate(data):
                for k, v in enumerate(row):
                    c.rect(x0 + k * cw, y - (r + 1) * rh, cw, rh)
                    c.drawString(x0 + k * cw + 4, y - (r + 1) * rh + 5, v)
            y -= 80
            c.drawString(72, y, "• first bullet point")
            c.drawString(72, y - 14, "• second bullet point")
        c.showPage()
    c.save()
    return str(path)


@pytest.fixture
def epub_file(tmp_path):
    path = tmp_path / "book.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:language>en</dc:language></metadata>'
                   '<manifest><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/></manifest>'
                   '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        z.writestr("OEBPS/ch1.xhtml", f"<html><body><h1>Chapter One</h1><p>{LOREM}</p></body></html>")
        z.writestr("OEBPS/ch2.xhtml", f"<html><body><h1>Chapter Two</h1><p>{LOREM}</p></body></html>")
    return str(path)


@pytest.fixture
def eml_file(tmp_path):
    m = EmailMessage()
    m["From"] = "Alice <alice@example.com>"
    m["To"] = "bob@example.com"
    m["Subject"] = "Launch update"
    m["Date"] = "Tue, 03 Nov 2026 10:00:00 +0000"
    m["Message-ID"] = "<abc123@example.com>"
    m.set_content("Hi Bob,\n\nThe launch is on track and the team has finished testing.\n\nThanks")
    m.add_alternative("<html><body><h2>Status</h2><p>The launch is on track and the team has finished testing.</p></body></html>",
                      subtype="html")
    path = tmp_path / "msg.eml"
    path.write_bytes(bytes(m))
    return str(path)
