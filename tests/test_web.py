import pytest
from fastapi.testclient import TestClient

from sheetagent import web


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "RUNS", tmp_path / "runs")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    return TestClient(web.app)


def test_page_and_health(client):
    assert "Build the report" in client.get("/").text
    assert client.get("/health").json() == {"status": "ok"}


def test_demo_run_returns_the_september_report(client):
    body = client.post("/api/demo").json()
    assert body["ok"] and body["period"] == "2026-09" and "up 6.2% on August 2026" in body["summary"]
    assert [t["label"] for t in body["tiles"]][:2] == ["Revenue (USD)", "Orders"]
    page = client.get(body["html"])
    assert page.status_code == 200 and "Cedar &amp; Stone" in page.text
    excel = client.get(body["excel"])
    assert excel.status_code == 200 and excel.content[:2] == b"PK"


def test_uploaded_files_are_cleaned(client, demo):
    root, truths = demo
    paths = sorted((root / "inbox" / "2026-09").iterdir()) + sorted((root / "reference").iterdir())
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
    body = client.post("/api/run", files=files, data={"period": ""}).json()
    assert body["ok"] and body["period"] == "2026-09"
    assert body["quality"]["rows_used"] == len(truths["2026-09"])


def test_bad_requests_are_refused_with_a_clear_message(client):
    assert client.post("/api/run", data={"period": "2026-09"}).status_code == 422
    files = [("files", ("report.pdf", b"x", "application/pdf"))]
    assert "only Excel" in client.post("/api/run", files=files).json()["detail"]
    files = [("files", ("a.csv", b"Date,SKU\n", "text/csv"))]
    assert client.post("/api/run", files=files, data={"period": "September"}).status_code == 422
    assert client.post("/api/run", data={"links": "http://169.254.169.254/latest"}).status_code == 422


def test_file_without_sales_gives_a_message_not_an_error(client):
    files = [("files", ("notes.csv", b"Topic,Count\nmeetings,3\ncalls,5\n", "text/csv"))]
    body = client.post("/api/run", files=files).json()
    assert body["ok"] is False and "No usable sales lines" in body["message"]


def test_uploaded_file_name_cannot_leave_the_run_folder(client, tmp_path):
    files = [("files", ("../../escape.csv", b"Topic,Count\na,1\nb,2\n", "text/csv"))]
    client.post("/api/run", files=files)
    assert not (tmp_path / "escape.csv").exists() and not (tmp_path.parent / "escape.csv").exists()


def test_downloads_are_limited_to_report_files(client):
    assert client.get("/runs/abcdefabcdef/2026-09/history.json").status_code == 404
    assert client.get("/runs/..%2f..%2fetc/2026-09/sales_report_2026-09.html").status_code == 404
    assert client.get("/runs/abcdefabcdef/2026-09/sales_report_2026-09.html").status_code == 404
