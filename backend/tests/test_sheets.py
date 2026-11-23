"""Sheet diffs preserve raw IDs and extraction markers; stop propagates through nested extraction."""
from types import SimpleNamespace

import pytest

from scraper import db, pipeline
from scraper.sources import sheets


@pytest.fixture
def conn(pg_url, monkeypatch):
    monkeypatch.setenv("INTERNAL_DOMAINS", "acme.test")
    db.migrate()
    with db.connect() as conn:
        yield conn


@pytest.fixture
def sheet_api(monkeypatch):
    state = {"rows": [["Name", "Email"], ["A", "a@client.test"], ["B", "b@client.test"]], "calls": [], "fail": False}
    class Sheets:
        def spreadsheets(self): return self
        def values(self): return self
        def get(self, **kw):
            def run():
                state["calls"].append(kw["spreadsheetId"])
                if state["fail"]:
                    raise RuntimeError("sheet unavailable")
                return {"values": state["rows"]} if "range" in kw else {
                    "properties": {"title": "Tracker"}, "sheets": [{"properties": {"title": "Clients"}}]}
            return SimpleNamespace(execute=run)
    monkeypatch.setattr(sheets, "service", lambda *_: Sheets())
    return state


@pytest.mark.parametrize("scrape", [False, True])
def test_sheet_diff_identical_changed_removed_and_shifted_rows(conn, monkeypatch, sheet_api, scrape):
    monkeypatch.setenv("DRIVE_SHEET_IDS", "s")

    def fetch(job_id):
        def log(line):
            pass

        log.job_id = job_id
        if scrape:
            pipeline.run_scrape(["sheets"], [], "2025-01-01", False, log)
        else:
            sheets.fetch_sheets(conn, ["s"], log, job_id=job_id)

    fetch(1)
    before = conn.execute("SELECT * FROM raw_items ORDER BY source_id").fetchall()
    assert {row["last_job"] for row in before} == {1}
    original = pipeline.extract_item
    seen = []
    def extract(row, *args):
        seen.append(row["id"])
        return original(row, *args)
    monkeypatch.setattr(pipeline, "extract_item", extract)
    fetch(2)
    assert conn.execute("SELECT * FROM raw_items ORDER BY source_id").fetchall() == before and seen == []
    sheet_api["rows"][1][0] = "A edited"
    fetch(3)
    assert seen == [before[0]["id"]]
    sheet_api["rows"] = [["Name", "Email"], ["B", "b@client.test"]]
    seen.clear()
    fetch(4)
    rows = conn.execute("SELECT * FROM raw_items").fetchall()
    assert len(rows) == 1 and rows[0]["id"] == before[0]["id"]
    assert rows[0]["last_job"] == 4
    assert seen == [before[0]["id"]]
    assert [r["email_key"] for r in conn.execute("SELECT email_key FROM candidates")] == ["b@client.test"]
    sheet_api["fail"] = True
    with pytest.raises(RuntimeError):
        fetch(5)
    conn.rollback()
    assert conn.execute("SELECT * FROM raw_items").fetchall() == rows


def test_stop_inside_scrape_extract_keeps_committed_sheets_and_batch(conn, monkeypatch, sheet_api):
    from scraper import app as api

    monkeypatch.setenv("DRIVE_SHEET_IDS", "first,second")
    monkeypatch.setattr(pipeline, "BATCH", 1)
    original = api.JobLog.checkpoint
    def stop_after_batch(logger):
        with db.connect() as observer:
            if observer.execute("SELECT count(*) AS n FROM raw_items WHERE extracted_at IS NOT NULL").fetchone()["n"] == 1:
                observer.execute("UPDATE jobs SET control='stop' WHERE id=%s", (logger.job_id,))
        original(logger)
    monkeypatch.setattr(api.JobLog, "checkpoint", stop_after_batch)
    monkeypatch.setattr(api, "_launch_job", api._run_job)
    api.start_job("scrape", {"sources": ["sheets"], "mailboxes": [], "since": "2025-01-01", "full": False})
    assert sheet_api["calls"] == ["first", "first", "second", "second"]
    assert conn.execute("SELECT status FROM jobs").fetchone()["status"] == "stopped"
    assert conn.execute("SELECT count(*) AS n FROM raw_items").fetchone()["n"] == 4
    assert conn.execute("SELECT count(*) AS n FROM raw_items WHERE extracted_at IS NOT NULL").fetchone()["n"] == 1
    assert not any("finished" in r["line"] for r in conn.execute("SELECT line FROM job_log"))
