"""Incremental extraction and candidate rebuilds against disposable Postgres."""

from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb

from scraper import db, pipeline
from scraper.sources import sheets


@pytest.fixture
def conn(pg_url, monkeypatch):
    monkeypatch.setenv("INTERNAL_DOMAINS", "acme.test")
    db.migrate()
    with db.connect() as conn:
        yield conn


def seed(conn, sid="s:Clients:2", email="bob@client.test", name="Bob"):
    db.upsert_raw(conn, "sheets", "", sid, {"sheet": "Tracker", "tab": "Clients", "row": 2,
                                           "record": {"Name": name, "Email": email}})
    item = conn.execute("SELECT id FROM raw_items WHERE source_id=%s", (sid,)).fetchone()["id"]
    conn.commit()
    return item


def candidates(conn):
    return {r["email_key"]: r for r in conn.execute("SELECT * FROM candidates")}


def test_rebuild_targets_includes_merged_ancestors():
    assert pipeline.rebuild_targets({"a", "x"}, {"a": "b", "b": "c", "d": "c"}) == {
        "c": {"a", "b", "c", "d"}, "x": {"x"}}


def test_incremental_replaces_old_and_new_person_and_skips_extracted(conn, monkeypatch):
    item = seed(conn)
    pipeline.run_extract(lambda _: None)
    assert conn.execute("SELECT extracted_at FROM raw_items WHERE id=%s", (item,)).fetchone()["extracted_at"]
    assert conn.execute("SELECT count(*) AS n FROM signals").fetchone()["n"] == 1
    original = pipeline.extract_item
    seen = []
    def extract(row, *args):
        seen.append(row["id"])
        return original(row, *args)
    monkeypatch.setattr(pipeline, "extract_item", extract)
    pipeline.run_extract(lambda _: None)
    assert seen == []
    seed(conn, email="ana@client.test", name="Ana")
    pipeline.run_extract(lambda _: None)
    assert seen == [item]
    assert set(candidates(conn)) == {"ana@client.test"}
    assert conn.execute("SELECT key FROM signals").fetchone()["key"] == "ana@client.test"


def test_full_reextract_deletes_pending_key_even_without_old_signals(conn, monkeypatch):
    seed(conn)
    pipeline.run_extract(lambda _: None)
    conn.execute("INSERT INTO candidates (email_key) VALUES ('orphan@client.test')")
    conn.commit()
    monkeypatch.setattr(pipeline, "extract_item", lambda *_: [])
    pipeline.run_extract(lambda _: None, full=True)
    assert candidates(conn) == {}
    assert conn.execute("SELECT count(*) AS n FROM signals").fetchone()["n"] == 0
    assert conn.execute("SELECT extracted_at FROM raw_items").fetchone()["extracted_at"]


@pytest.mark.parametrize("state", ["approved", "rejected", "merged"])
def test_reviewed_records_are_not_overwritten(conn, state):
    seed(conn)
    pipeline.run_extract(lambda _: None)
    sentinel = {"name": {"value": "Reviewed", "source": "human", "confidence": 1}}
    conn.execute("UPDATE candidates SET review_state=%s, fields=%s, reviewed=%s", (state, Jsonb(sentinel), Jsonb({"name": "Edits"})))
    conn.commit()
    seed(conn, name="New name")
    pipeline.run_extract(lambda _: None, full=True)
    row = candidates(conn)["bob@client.test"]
    assert row["fields"] == sentinel and row["reviewed"] == {"name": "Edits"}
    assert row["review_state"] == state


def test_parser_failure_keeps_signals_and_retries_next_run(conn, monkeypatch):
    item = seed(conn)
    pipeline.run_extract(lambda _: None)
    before = conn.execute("SELECT * FROM signals").fetchall()
    seed(conn, name="Changed")
    original = pipeline.extract_item
    calls = []
    def broken(row, *_):
        calls.append(row["id"])
        raise ValueError("fixture parser failure")
    monkeypatch.setattr(pipeline, "extract_item", broken)
    pipeline.run_extract(lambda _: None)
    assert calls == [item]
    row = conn.execute("SELECT * FROM raw_items").fetchone()
    assert row["extracted_at"] is None and "fixture parser failure" in row["extract_error"]
    assert conn.execute("SELECT * FROM signals").fetchall() == before
    assert candidates(conn)["bob@client.test"]["fields"]["name"]["value"] == "Bob"
    monkeypatch.setattr(pipeline, "extract_item", original)
    pipeline.run_extract(lambda _: None)
    row = conn.execute("SELECT * FROM raw_items").fetchone()
    assert row["extracted_at"] and row["extract_error"] is None
    assert candidates(conn)["bob@client.test"]["fields"]["name"]["value"] == "Changed"


def test_crash_rolls_back_markers_signals_and_people_of_failed_batch(conn, monkeypatch):
    for i in range(501):
        db.upsert_raw(conn, "sheets", "", str(i), {"sheet": "T", "tab": "A", "row": i + 2,
                      "record": {"Name": f"P{i}", "Email": f"p{i}@client.test"}})
    conn.commit()
    original = pipeline.rebuild
    calls = []
    def crash(conn, keys, rules, now):
        calls.append(set(keys))
        original(conn, keys, rules, now)
        if len(calls) == 2:
            raise RuntimeError("crash after rebuild")
    monkeypatch.setattr(pipeline, "rebuild", crash)
    with pytest.raises(RuntimeError, match="crash"):
        pipeline.run_extract(lambda _: None)
    assert conn.execute("SELECT count(*) AS n FROM raw_items WHERE extracted_at IS NOT NULL").fetchone()["n"] == 500
    assert len(candidates(conn)) == 500
    assert conn.execute("SELECT count(*) AS n FROM signals").fetchone()["n"] == 500
    monkeypatch.setattr(pipeline, "rebuild", original)
    pipeline.run_extract(lambda _: None)
    assert len(candidates(conn)) == 501
    assert conn.execute("SELECT count(*) AS n FROM raw_items WHERE extracted_at IS NULL").fetchone()["n"] == 0


def test_changed_merged_source_rebuilds_surviving_target(conn):
    seed(conn)
    seed(conn, sid="other", email="ana@client.test", name="Ana")
    pipeline.run_extract(lambda _: None)
    conn.execute("UPDATE candidates SET review_state='merged', merged_into=(SELECT id FROM candidates WHERE email_key='ana@client.test') WHERE email_key='bob@client.test'")
    conn.commit()
    seed(conn, name="Bob edited")
    pipeline.run_extract(lambda _: None)
    rows = candidates(conn)
    assert rows["bob@client.test"]["review_state"] == "merged"
    assert rows["ana@client.test"]["fields"]["emails"]["value"] == ["ana@client.test", "bob@client.test"]


def test_sheet_row_removal_rebuilds_removed_person(conn, monkeypatch):
    seed(conn)
    pipeline.run_extract(lambda _: None)
    class FakeSheet:
        def spreadsheets(self): return self
        def values(self): return self
        def get(self, **kw):
            return SimpleNamespace(execute=lambda: {"values": [["Name", "Email"], ["Ana", "ana@client.test"]]} if "range" in kw else {
                "properties": {"title": "Tracker"}, "sheets": [{"properties": {"title": "Clients"}}]})
    monkeypatch.setattr(sheets, "service", lambda *_: FakeSheet())
    sheets.fetch_sheets(conn, ["s"], lambda _: None)
    pipeline.run_extract(lambda _: None)
    assert set(candidates(conn)) == {"ana@client.test"}
    assert conn.execute("SELECT key FROM signals").fetchone()["key"] == "ana@client.test"


def test_scrape_exposes_rows_per_mailbox_and_continues_after_failure(conn, monkeypatch):
    seen = []
    def fetch(connection, mailbox, *_, **kwargs):
        with db.connect() as observer:
            seen.append(set(candidates(observer)))
        db.upsert_raw(connection, "sheets", "", mailbox, {"sheet": "T", "tab": "A", "row": 2,
                      "record": {"Name": mailbox, "Email": mailbox}})
        connection.commit()
    def contacts(*_, **kwargs):
        raise RuntimeError("unlicensed")
    monkeypatch.setattr(pipeline.gmail, "fetch_mailbox", fetch)
    monkeypatch.setattr(pipeline.contacts, "fetch_contacts", contacts)
    log = []
    pipeline.run_scrape(["gmail", "contacts"], ["a@client.test", "b@client.test"], "2025-01-01", False, log.append)
    assert seen == [set(), {"a@client.test"}]
    assert set(candidates(conn)) == {"a@client.test", "b@client.test"}
    assert sum("FAILED" in line for line in log) == 2


def test_sheet_refresh_preserves_kept_ids_and_merges(conn, monkeypatch):
    seed(conn, sid="s:Clients:2", email="a@client.test", name="A")
    seed(conn, sid="s:Clients:3", email="bob@client.test", name="Bob")
    pipeline.run_extract(lambda _: None)
    before = candidates(conn)
    target = before["bob@client.test"]["id"]
    conn.execute("UPDATE candidates SET review_state='merged', merged_into=%s WHERE email_key='a@client.test'", (target,))
    conn.commit()
    class FakeSheet:
        def spreadsheets(self): return self
        def values(self): return self
        def get(self, **kw):
            return SimpleNamespace(execute=lambda: {"values": [["Name", "Email"], ["A", "a@client.test"], ["Bob", "bob@client.test"]]} if "range" in kw else {
                "properties": {"title": "Tracker"}, "sheets": [{"properties": {"title": "Clients"}}]})
    monkeypatch.setattr(sheets, "service", lambda *_: FakeSheet())
    sheets.fetch_sheets(conn, ["s"], lambda _: None)
    after = candidates(conn)
    assert after["bob@client.test"]["id"] == target
    assert after["a@client.test"]["merged_into"] == target
    assert after["bob@client.test"]["fields"]["emails"]["value"] == ["a@client.test", "bob@client.test"]


def test_restart_after_full_reset_cleans_pending_keys_without_signals(conn, monkeypatch):
    seed(conn)
    pipeline.run_extract(lambda _: None)
    conn.execute("INSERT INTO candidates (email_key) VALUES ('orphan@client.test')")
    conn.commit()
    original = pipeline.rebuild
    def crash(*_):
        raise RuntimeError("interrupted full run")
    monkeypatch.setattr(pipeline, "rebuild", crash)
    with pytest.raises(RuntimeError, match="interrupted full"):
        pipeline.run_extract(lambda _: None, full=True)
    monkeypatch.setattr(pipeline, "rebuild", original)
    pipeline.run_extract(lambda _: None)
    assert set(candidates(conn)) == {"bob@client.test"}


def test_rebuild_without_signals_preserves_merge_target_and_mapping(conn):
    from datetime import datetime, timezone
    from scraper.config import load_rules

    seed(conn)
    seed(conn, sid="a", email="a@client.test", name="A")
    pipeline.run_extract(lambda _: None)
    target = candidates(conn)["bob@client.test"]["id"]
    conn.execute("UPDATE candidates SET review_state='merged', merged_into=%s WHERE email_key='a@client.test'", (target,))
    conn.execute("DELETE FROM raw_items")
    pipeline.rebuild(conn, {"a@client.test", "bob@client.test"}, load_rules(), datetime.now(timezone.utc))
    conn.commit()
    assert candidates(conn)["a@client.test"]["merged_into"] == target
    assert candidates(conn)["bob@client.test"]["id"] == target


def test_stored_signals_rebuild_details_and_merge_reopen_recompute_without_overwriting_reviewed(conn):
    from datetime import datetime, timedelta, timezone
    from fastapi.testclient import TestClient
    from scraper.app import app
    from scraper.config import load_rules
    from scraper.extract import signal
    from scraper.merge import to_json

    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    a = seed(conn, email="a@client.test", sid="a")
    b = seed(conn, email="b@client.test", sid="b")
    for item, key, mailbox, day in [(a, "a@client.test", "sales@acme.test", 0), (b, "b@client.test", "billing@acme.test", 2)]:
        s = signal(key, "gmail", item, stage="payment_confirmed", thread=f"{mailbox}:t", department="Sales",
                   date=now + timedelta(days=day), snippet=key, services=["Website"],
                   amounts=[{"amount": 1000, "currency": "PHP", "total": True}])
        conn.execute("INSERT INTO signals (raw_item_id, key, data) VALUES (%s,%s,%s)", (item, key, Jsonb(to_json(s))))
    conn.execute("UPDATE raw_items SET extracted_at=now()")
    pipeline.rebuild(conn, {"a@client.test", "b@client.test"}, load_rules(), now)
    conn.commit()
    before = candidates(conn)
    a_id, b_id = before["a@client.test"]["id"], before["b@client.test"]["id"]
    assert before["a@client.test"]["fields"]["payments"]["value"][0]["raw_item_id"] == a
    local = TestClient(app, base_url="http://localhost")
    gui = {"X-Local-GUI": "1"}
    assert local.post(f"/api/candidates/{a_id}/merge", json={"into_id": b_id}, headers=gui).status_code == 200
    merged = candidates(conn)["b@client.test"]["fields"]
    assert len(merged["payments"]["value"]) == 2
    assert sum(h["messages"] for h in merged["handlers"]["value"]) == 2
    assert local.post(f"/api/candidates/{a_id}/reopen", headers=gui).status_code == 200
    reopened = candidates(conn)
    assert len(reopened["a@client.test"]["fields"]["payments"]["value"]) == 1
    assert len(reopened["b@client.test"]["fields"]["payments"]["value"]) == 1
    assert sum(h["messages"] for h in reopened["b@client.test"]["fields"]["handlers"]["value"]) == 1
    conn.execute("UPDATE candidates SET review_state='approved', reviewed=%s WHERE id=%s", (Jsonb({"name": "Reviewer"}), b_id))
    conn.commit()
    saved = candidates(conn)["b@client.test"]["fields"]
    conn.execute("UPDATE signals SET data=jsonb_set(data, '{services}', '[\"Changed\"]')")
    pipeline.rebuild(conn, {"a@client.test", "b@client.test"}, load_rules(), now)
    conn.commit()
    assert candidates(conn)["b@client.test"]["fields"] == saved
    assert candidates(conn)["b@client.test"]["reviewed"] == {"name": "Reviewer"}


def test_full_reset_notifies_job_only_after_reset_commit(conn, monkeypatch):
    from scraper.app import JobLog

    seed(conn)
    pipeline.run_extract(lambda _: None)
    jid = conn.execute("INSERT INTO jobs (name, params, status) VALUES ('extract', '{\"full\": true}', 'running') RETURNING id").fetchone()["id"]
    conn.commit()
    log = JobLog(jid)
    original = log.full_reset_done
    def stopped():
        with db.connect() as observer:
            assert observer.execute("SELECT count(*) AS n FROM signals").fetchone()["n"] == 0
            assert observer.execute("SELECT count(*) AS n FROM raw_items WHERE extracted_at IS NULL").fetchone()["n"] == 1
        original()
        raise RuntimeError("simulated termination after reset")
    monkeypatch.setattr(log, "full_reset_done", stopped)
    with pytest.raises(RuntimeError, match="simulated termination"):
        pipeline.run_extract(log, full=True)
    assert conn.execute("SELECT params FROM jobs").fetchone()["params"]["full_reset_done"] is True


def test_job_counts_failed_mailbox_then_continues_and_extracts_good_one(conn, monkeypatch):
    from scraper import app as app_module

    def fetch(connection, mailbox, *_, **kwargs):
        if mailbox == "bad@acme.test":
            raise RuntimeError("unlicensed")
        db.upsert_raw(connection, "sheets", "", "good", {"sheet": "T", "tab": "A", "row": 2,
                      "record": {"Name": "Good", "Email": "good@client.test"}})
        connection.commit()
    monkeypatch.setattr(pipeline.gmail, "fetch_mailbox", fetch)
    monkeypatch.setattr(app_module, "_launch_job", app_module._run_job)
    app_module.start_job("scrape", {"sources": ["gmail"], "mailboxes": ["bad@acme.test", "good@acme.test"],
                                   "since": "2020-01-01", "full": False})
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["status"] == "done_with_errors" and row["failures"] == 1 and row["error"] is None
    assert set(candidates(conn)) == {"good@client.test"}
    assert any("bad@acme.test gmail: FAILED" in r["line"] for r in conn.execute("SELECT line FROM job_log"))


def test_extraction_failures_make_job_done_with_errors(conn, monkeypatch):
    from scraper import app as app_module

    db.upsert_raw(conn, "sheets", "", "bad", {"record": None})
    conn.commit()
    monkeypatch.setattr(app_module, "_launch_job", app_module._run_job)
    app_module.start_job("extract", {"full": False})
    assert conn.execute("SELECT status, failures FROM jobs").fetchone() == {"status": "done_with_errors", "failures": 1}
    assert conn.execute("SELECT extract_error FROM raw_items").fetchone()["extract_error"]


def test_full_reset_failure_before_commit_does_not_set_resume_marker(conn, monkeypatch):
    from contextlib import contextmanager
    from scraper.app import JobLog

    seed(conn)
    pipeline.run_extract(lambda _: None)
    conn.execute("INSERT INTO jobs (name, params, status) VALUES ('extract', '{\"full\": true}', 'running')")
    conn.commit()
    original = db.connect
    class Connection:
        def __init__(self, real): self.real = real
        def __getattr__(self, name): return getattr(self.real, name)
        def execute(self, query, args=None):
            if query.startswith("UPDATE raw_items SET extracted_at=NULL"):
                raise RuntimeError("reset failed before commit")
            return self.real.execute(query, args)
    @contextmanager
    def connect():
        with original() as real:
            yield Connection(real)
    monkeypatch.setattr(db, "connect", connect)
    with pytest.raises(RuntimeError, match="before commit"):
        pipeline.run_extract(JobLog(1), full=True)
    assert conn.execute("SELECT params FROM jobs").fetchone()["params"] == {"full": True}
    assert conn.execute("SELECT count(*) AS n FROM signals").fetchone()["n"] == 1
    assert conn.execute("SELECT extracted_at FROM raw_items").fetchone()["extracted_at"]


@pytest.mark.parametrize("action", ["pause", "stop"])
def test_extraction_batch_controls_and_full_resume_keep_completed_signals(conn, monkeypatch, action):
    import threading
    import time
    from fastapi.testclient import TestClient
    from scraper import app as api
    from psycopg.types.json import Jsonb

    db.upsert_raw_many(conn, "sheets", "", [(str(i), {"sheet": "T", "tab": "A", "row": i + 2,
        "record": {"Name": f"P{i}", "Email": f"p{i}@client.test"}}) for i in range(3)])
    params = {"full": True}
    jid = conn.execute("INSERT INTO jobs(name,status,params) VALUES ('extract','running',%s) RETURNING id", (Jsonb(params),)).fetchone()["id"]
    conn.commit()
    monkeypatch.setattr(pipeline, "BATCH", 2)
    monkeypatch.setattr(api, "PAUSE_POLL_SECONDS", .01)
    original = api.JobLog.checkpoint
    requested = threading.Event()
    def checkpoint(logger):
        with db.connect() as observer:
            n = observer.execute("SELECT count(*) AS n FROM raw_items WHERE extracted_at IS NOT NULL").fetchone()["n"]
            if n == 2 and not requested.is_set():
                observer.execute("UPDATE jobs SET control=%s WHERE id=%s", (action, jid))
                requested.set()
        original(logger)
    monkeypatch.setattr(api.JobLog, "checkpoint", checkpoint)
    worker = threading.Thread(target=api._run_job, args=(jid, "extract", params))
    client = TestClient(api.app, base_url="http://localhost")
    worker.start()
    before = None
    try:
        assert requested.wait(timeout=5)
        if action == "pause":
            deadline = time.monotonic() + 5
            while conn.execute("SELECT status FROM jobs WHERE id=%s", (jid,)).fetchone()["status"] != "paused":
                assert time.monotonic() < deadline
                time.sleep(.01)
            assert conn.execute("SELECT count(*) AS n FROM raw_items WHERE extracted_at IS NOT NULL").fetchone()["n"] == 2
            assert client.post(f"/api/jobs/{jid}/continue", headers={"X-Local-GUI": "1"}).status_code == 200
        worker.join(timeout=5)
        assert not worker.is_alive()
        before = conn.execute("SELECT * FROM signals ORDER BY id").fetchall()
    finally:
        if worker.is_alive():
            client.post(f"/api/jobs/{jid}/stop", headers={"X-Local-GUI": "1"})
            worker.join(timeout=5)
    if action == "stop":
        assert len(before) == 2
        assert conn.execute("SELECT status,params FROM jobs").fetchone() == {
            "status": "stopped", "params": {"full": True, "full_reset_done": True}}
        monkeypatch.setattr(api, "_launch_job", api._run_job)
        assert client.post(f"/api/jobs/{jid}/resume", headers={"X-Local-GUI": "1"}).status_code == 200
        assert conn.execute("SELECT * FROM signals ORDER BY id LIMIT 2").fetchall() == before
    assert conn.execute("SELECT count(*) AS n FROM raw_items WHERE extracted_at IS NOT NULL").fetchone()["n"] == 3
    assert conn.execute("SELECT status FROM jobs ORDER BY id DESC LIMIT 1").fetchone()["status"] == "done"


@pytest.mark.parametrize("sources", [["gmail"], ["contacts"], ["gmail", "contacts"]])
def test_stopped_scrape_resume_skips_done_mailboxes_but_new_run_does_not(conn, monkeypatch, sources):
    from fastapi.testclient import TestClient
    from scraper import app as api

    seen = []
    def gmail_fetch(connection, mailbox, *args, **kwargs):
        seen.append(("gmail", mailbox))
        connection.commit()
    def contacts_fetch(connection, mailbox, *args, **kwargs):
        seen.append(("contacts", mailbox))
        connection.commit()
    monkeypatch.setattr(pipeline.gmail, "fetch_mailbox", gmail_fetch)
    monkeypatch.setattr(pipeline.contacts, "fetch_contacts", contacts_fetch)
    original = api.JobLog.mailbox_done
    stopped = []
    def done(logger, mailbox):
        original(logger, mailbox)
        if mailbox == "a" and not stopped:
            stopped.append(True)
            with db.connect() as control:
                control.execute("UPDATE jobs SET control='stop' WHERE id=%s", (logger.job_id,))
    monkeypatch.setattr(api.JobLog, "mailbox_done", done)
    monkeypatch.setattr(api, "_launch_job", api._run_job)
    params = {"sources": sources, "mailboxes": ["a", "b"], "since": "2020-01-01", "full": False}
    jid = api.start_job("scrape", params)
    row = conn.execute("SELECT * FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row["status"] == "stopped" and row["params"]["done_mailboxes"] == ["a"]
    client = TestClient(api.app, base_url="http://localhost")
    assert client.post(f"/api/jobs/{jid}/resume", headers={"X-Local-GUI": "1"}).status_code == 200
    assert seen == [(source, mailbox) for mailbox in ("a", "b") for source in sources]
    latest = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
    assert latest["params"]["done_mailboxes"] == ["a", "b"]
    api.start_job("scrape", params)
    assert seen == [(source, mailbox) for mailbox in ("a", "b", "a", "b") for source in sources]


def test_failed_mailbox_source_is_not_marked_done(conn, monkeypatch):
    from scraper import app as api

    def fetch(connection, mailbox, *args, **kwargs):
        if mailbox == "bad":
            raise RuntimeError("source failed")
        connection.commit()
    monkeypatch.setattr(pipeline.gmail, "fetch_mailbox", fetch)
    monkeypatch.setattr(api, "_launch_job", api._run_job)
    api.start_job("scrape", {"sources": ["gmail"], "mailboxes": ["bad", "good"], "since": "2020-01-01", "full": False})
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["params"]["done_mailboxes"] == ["good"] and row["status"] == "done_with_errors"


def test_job_stopped_from_parser_is_not_swallowed_as_an_extraction_error(conn, monkeypatch):
    from scraper.db import JobStopped

    seed(conn)
    def stopped(*args):
        raise JobStopped()
    monkeypatch.setattr(pipeline, "extract_item", stopped)
    with pytest.raises(JobStopped):
        pipeline.run_extract(lambda _: None)
    assert conn.execute("SELECT extracted_at,extract_error FROM raw_items").fetchone() == {"extracted_at": None, "extract_error": None}
