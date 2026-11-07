from app.extract import extract
from docparse import partition_md

INVOICE = """# INVOICE #4521

Bill to: Jane Smith

Due date: March 15, 2026

| Item | Qty | Price |
|---|---|---|
| Printer paper | 10 | $5.00 |
| Ink cartridge | 2 | $30.00 |

| Subtotal | $110.00 |
|---|---|
| Tax | $8.80 |
| Total | $118.80 |

Questions? billing@acme.example or +1 (555) 201-3344
"""

FIELDS = [
    {"name": "Invoice number", "type": "id", "aliases": ["invoice", "invoice no"]},
    {"name": "Customer", "aliases": ["bill to", "billed to"]},
    {"name": "Due date", "type": "date"},
    {"name": "Total", "type": "money", "aliases": ["amount due", "total due"]},
    {"name": "Tax", "type": "money", "aliases": ["vat"]},
    {"name": "Email", "type": "email"},
    {"name": "Phone", "type": "phone"},
    {"name": "Line items", "type": "list"},
    {"name": "Purchase order", "type": "id", "aliases": ["po number"]},
]


def test_invoice_fields():
    els = [e.to_dict() for e in partition_md(text=INVOICE)]
    got = {r["name"]: r for r in extract(els, FIELDS)}
    assert got["Invoice number"]["value"] == "4521"
    assert got["Customer"]["value"] == "Jane Smith"
    assert got["Due date"]["value"] == "March 15, 2026"
    assert got["Total"]["value"] == "$118.80" and got["Total"]["confidence"] >= 0.8
    assert got["Tax"]["value"] == "$8.80"
    assert got["Email"]["value"] == "billing@acme.example"
    assert "555" in got["Phone"]["value"]
    items = got["Line items"]["value"]
    assert items[1] == {"Item": "Ink cartridge", "Qty": "2", "Price": "$30.00"}
    assert got["Purchase order"]["value"] is None and got["Purchase order"]["method"] == "not found"
    # every found value points at a real element
    ids = {e["element_id"] for e in els}
    assert all(r["element_id"] in ids for r in got.values() if r["value"] is not None)


def test_suggest_fields_from_structure():
    from app.extract import suggest

    md = INVOICE + "\nInvoice date: February 13, 2026\n\nPayment terms: 30 days\n"
    s = suggest([e.to_dict() for e in partition_md(text=md)])
    assert s["doc_type"] == "invoice"
    by = {f["name"]: f for f in s["fields"]}
    assert by["Due date"]["type"] == "date" and by["Due date"]["example"] == "March 15, 2026"
    assert by["Invoice date"]["type"] == "date"
    assert by["Total"]["type"] == "money" and by["Total"]["example"] == "$118.80"
    assert by["Bill to"]["type"] == "text" and by["Payment terms"]["example"] == "30 days"
    assert any(f["type"] == "list" for f in s["fields"])
    assert by["Email"]["example"] == "billing@acme.example"


def test_suggest_skips_data_rows_and_types_from_value_start():
    from app.extract import extract, suggest

    md = INVOICE + "\nPayment terms: 30 days. Questions? billing@acme.example\n"
    els = [e.to_dict() for e in partition_md(text=md)]
    by = {f["name"]: f for f in suggest(els)["fields"]}
    assert by["Payment terms"]["type"] == "text" and by["Payment terms"]["example"] == "30 days"
    assert "Printer paper" not in by and "Item" not in by
    got = extract(els, [{"name": "Payment terms"}])[0]
    assert got["value"] == "30 days"


def _form_pdf(path):
    """A small fillable form: labels printed on the page, values only in AcroForm fields."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    W, H = A4
    c.setFont("Helvetica-Bold", 7)
    for x, label in ((57, "Flight"), (214, "Class Of Travel"), (355, "Seat")):
        c.drawString(x, H - 166, label)
    for x, label in ((57, "Booking Reference"), (214, "Status")):
        c.drawString(x, H - 266, label)
    form = c.acroForm
    for name, value, x, y in (("flight", "QR 0517", 55, H - 191), ("cabin", "ECONOMY", 212, H - 191), ("seat", "22D", 352, H - 191),
                              ("ref", "RWF6GP", 55, H - 291), ("status", "Checked In", 212, H - 291)):
        form.textfield(name=name, value=value, x=x, y=y, width=140, height=22, fontName="Helvetica-Bold", fontSize=12, borderWidth=0)
    c.drawString(57, H - 340, "Important Information")
    form.textfield(name="info", value="1. Arrive at least three hours before your flight because there may be delays at the airport.",
                   x=55, y=H - 420, width=200, height=70, fontName="Helvetica", fontSize=7, borderWidth=0,
                   fieldFlags="multiline")
    c.showPage()
    c.save()


def test_form_field_values_are_extracted(tmp_path):
    import pytest

    pytest.importorskip("reportlab")
    from app.extract import extract, suggest
    from docparse import partition

    p = tmp_path / "form.pdf"
    _form_pdf(p)
    els = [e.to_dict() for e in partition(filename=str(p))]
    texts = [e["text"] for e in els]
    for v in ("QR 0517", "ECONOMY", "22D", "RWF6GP", "Checked In"):
        assert v in texts, v
    assert not any(e["type"] == "Title" and e["text"] in ("Flight", "Seat", "QR 0517") for e in els)
    info = [t for t in texts if t.startswith("1. Arrive")]
    assert info and info[0].endswith("airport.")  # wrapped field text stays one item
    got = {r["name"]: r["value"] for r in extract(els, [{"name": "Flight"}, {"name": "Seat"}, {"name": "Booking Reference", "type": "id"}, {"name": "Status"}])}
    assert got == {"Flight": "QR 0517", "Seat": "22D", "Booking Reference": "RWF6GP", "Status": "Checked In"}
    names = {f["name"] for f in suggest(els)["fields"]}
    assert {"Flight", "Seat", "Booking Reference"} <= names and "ECONOMY" not in names
