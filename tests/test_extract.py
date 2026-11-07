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
