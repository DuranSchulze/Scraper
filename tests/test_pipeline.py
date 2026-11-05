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
    def fetch(connection, mailbox, *_):
        with db.connect() as observer:
            seen.append(set(candidates(observer)))
        db.upsert_raw(connection, "sheets", "", mailbox, {"sheet": "T", "tab": "A", "row": 2,
                      "record": {"Name": mailbox, "Email": mailbox}})
        connection.commit()
    def contacts(*_):
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
