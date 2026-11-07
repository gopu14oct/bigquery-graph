"""API tests for docparse Studio (the mobile web app)."""

import io
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCPARSE_APP_DATA", str(tmp_path / "data"))
    import importlib

    import app.server as server
    importlib.reload(server)
    return TestClient(server.app)


def wait_done(client, doc_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/docs/{doc_id}").json()
        if d["meta"]["status"] in ("done", "error"):
            return d
        time.sleep(0.2)
    raise AssertionError("parse did not finish")


def test_upload_parse_validate_export(client, pdf_file):
    with open(pdf_file, "rb") as f:
        r = client.post("/api/docs", files={"file": ("paper.pdf", f, "application/pdf")})
    assert r.status_code == 200
    doc = wait_done(client, r.json()["id"])
    assert doc["meta"]["status"] == "done"
    els = doc["result"]["elements"]
    assert any(e["type"] == "Title" for e in els)
    v = doc["result"]["validation"]
    assert 0 <= v["score"] <= 100 and {c["id"] for c in v["checks"]} >= {"coverage", "empty_pages", "fragments"}
    doc_id = doc["meta"]["id"]
    assert client.get(f"/api/docs/{doc_id}/pages/1.png").headers["content-type"] == "image/png"
    for fmt in ("json", "jsonl", "csv", "markdown", "bigquery", "reviews"):
        assert client.get(f"/api/docs/{doc_id}/export", params={"fmt": fmt}).status_code == 200
    md = client.get(f"/api/docs/{doc_id}/export", params={"fmt": "markdown"}).text
    assert "# A Study of Parsing" in md


def test_reviews_and_chunks(client, docx_file):
    with open(docx_file, "rb") as f:
        doc_id = client.post("/api/docs", files={"file": ("r.docx", f)}).json()["id"]
    doc = wait_done(client, doc_id)
    eid = doc["result"]["elements"][1]["element_id"]
    r = client.post(f"/api/docs/{doc_id}/reviews", json={"element_id": eid, "verdict": "wrong", "corrected_type": "Title"})
    assert r.json()[eid]["corrected_type"] == "Title"
    exp = client.get(f"/api/docs/{doc_id}/export", params={"fmt": "reviews"}).json()
    assert exp["reviews"][0]["element_id"] == eid
    client.post(f"/api/docs/{doc_id}/reviews", json={"element_id": eid})  # clear
    assert eid not in client.get(f"/api/docs/{doc_id}").json()["reviews"]
    ch = client.post(f"/api/docs/{doc_id}/chunks", json={"strategy": "by_title", "max_characters": 300}).json()
    assert ch["stats"]["count"] > 0 and ch["stats"]["max"] <= 300
    assert client.post(f"/api/docs/{doc_id}/chunks", json={"max_characters": 100, "overlap": 200}).status_code == 400


def test_rejects_bad_input(client):
    assert client.post("/api/docs", files={"file": ("x.bin", io.BytesIO(b""))}).status_code == 400
    assert client.get("/api/docs/../../etc").status_code == 404
    assert client.get("/api/docs/zzzzzzzzzzzz").status_code == 404
