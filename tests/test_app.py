from fastapi.testclient import TestClient

from scraper.app import app

local = TestClient(app, base_url="http://localhost")


def test_gui_page_served_on_localhost():
    r = local.get("/")
    assert r.status_code == 200 and "Workspace Scraper" in r.text


def test_other_hosts_rejected():
    assert TestClient(app, base_url="http://evil.test").get("/").status_code == 400


def test_post_without_gui_header_rejected():
    assert local.post("/api/extract").status_code == 403


from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading

import pytest
from psycopg.types.json import Jsonb

from scraper import db, pipeline
from scraper import app as app_module

GUI = {"X-Local-GUI": "1"}


@pytest.fixture
def conn(pg_url, monkeypatch):
    monkeypatch.setenv("INTERNAL_DOMAINS", "acme.test")
    db.migrate()
    with db.connect() as conn:
        yield conn


def candidate(conn, email="bob@client.test", name="Bob"):
    db.upsert_raw(conn, "sheets", "", email, {"sheet": "T", "tab": "A", "row": 2,
                                           "record": {"Name": name, "Email": email}})
    conn.commit()
    pipeline.run_extract(lambda _: None)
    return conn.execute("SELECT id FROM candidates WHERE email_key=%s", (email,)).fetchone()["id"]


def edited(name="Reviewer edits"):
    return {"name": name, "emails": ["edited@client.test"], "phones": ["09175551234"],
            "company": "Edited Co", "status": "active", "stage": "paid", "services": ["SEO"],
            "department": "Sales", "engagement_paid": True, "first_contact": "2026-01-01T00:00:00Z",
            "last_contact": "2026-11-01T00:00:00Z", "notes": "Checked manually", "amounts": [
                {"stage": "invoiced", "amount": 25000, "currency": "PHP", "date": None, "raw_item_id": 1}]}


def approve(cid, body=None):
    return local.post(f"/api/candidates/{cid}/approve", json=body or edited(), headers=GUI)


def publish():
    return local.post("/api/publish", headers=GUI)


def test_approve_stages_edits_and_publish_is_idempotent_then_updates_same_client(conn):
    cid = candidate(conn)
    assert approve(cid).json() == {"ok": True}
    assert conn.execute("SELECT count(*) AS n FROM clients").fetchone()["n"] == 0
    detail = local.get(f"/api/candidates/{cid}").json()
    assert detail["review_state"] == "approved" and detail["client_id"] is None
    assert detail["reviewed"]["name"] == "Reviewer edits"
    assert detail["reviewed"]["amounts"] == edited()["amounts"]
    assert detail["reviewed_by"] and detail["reviewed_at"] and detail["published_at"] is None
    assert local.get("/api/status").json()["ready_to_publish"] == 1
    assert publish().json() == {"published": 1, "inserted": 1, "updated": 0}
    row = conn.execute("SELECT * FROM clients").fetchone()
    assert row["name"] == "Reviewer edits" and row["stage"] == "paid" and row["engagement_paid"] is True
    assert row["amounts"] == edited()["amounts"]
    assert row["first_contact"] == datetime(2026, 1, 1, tzinfo=timezone.utc)
    first_id = row["id"]
    assert publish().json() == {"published": 0, "inserted": 0, "updated": 0}
    assert local.get("/api/status").json()["ready_to_publish"] == 0
    assert approve(cid, edited("Second edit")).json() == {"ok": True}
    assert conn.execute("SELECT name FROM clients").fetchone()["name"] == "Reviewer edits"
    assert publish().json() == {"published": 1, "inserted": 0, "updated": 1}
    assert conn.execute("SELECT id, name FROM clients").fetchall() == [{"id": first_id, "name": "Second edit"}]
    assert local.get(f"/api/candidates/{cid}").json()["published_at"]


def test_publish_failure_rolls_back_clients_and_markers(conn):
    a = candidate(conn, name="Good")
    b = candidate(conn, email="b@client.test", name="Bad")
    assert approve(a, edited("Good")).status_code == 200
    assert approve(b, edited("Bad")).status_code == 200
    conn.execute("ALTER TABLE clients ADD CONSTRAINT injected_failure CHECK (name <> 'Bad')")
    conn.commit()
    with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as client:
        assert client.post("/api/publish", headers=GUI).status_code == 500
    assert conn.execute("SELECT count(*) AS n FROM clients").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM candidates WHERE published_at IS NOT NULL OR client_id IS NOT NULL").fetchone()["n"] == 0
    conn.execute("ALTER TABLE clients DROP CONSTRAINT injected_failure")
    conn.commit()
    assert publish().json() == {"published": 2, "inserted": 2, "updated": 0}


def test_simultaneous_publishes_insert_once(conn):
    cid = candidate(conn)
    approve(cid)
    barrier = threading.Barrier(2)
    def request():
        barrier.wait(timeout=5)
        return publish().json()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: request(), range(2)))
    assert sorted(r["published"] for r in results) == [0, 1]
    assert conn.execute("SELECT count(*) AS n FROM clients").fetchone()["n"] == 1


def test_status_list_and_detail_contract(conn):
    cid = candidate(conn)
    conn.execute("UPDATE raw_items SET extract_error='parse failed', extracted_at=NULL")
    conn.execute("UPDATE candidates SET fields=fields || %s", (Jsonb({
        "stage": {"value": "invoiced"}, "engagement_paid": {"value": False},
        "needs_check": {"value": ["Invoice amount missing."]}, "department": {"value": "Sales"}}),))
    conn.commit()
    status = local.get("/api/status").json()
    assert status["extract_failed"] == 1 and status["ready_to_publish"] == 0
    assert status["candidates"] == {"pending": 1} and status["raw_items"] == {"sheets": 1}
    rows = local.get("/api/candidates?stage=invoiced&department=Sales&q=bob").json()
    assert len(rows) == 1 and rows[0]["id"] == cid
    assert rows[0]["paid"] is False and rows[0]["needs_check"] == 1 and rows[0]["stage"] == "invoiced"
    assert local.get("/api/candidates?stage=paid").json() == []
    detail = local.get(f"/api/candidates/{cid}").json()
    assert all(key in detail for key in ("reviewed", "reviewed_by", "reviewed_at", "published_at"))
    assert detail["reviewed"] is None


def test_extract_full_flag_reaches_pipeline(conn, monkeypatch):
    seen = []
    monkeypatch.setattr(app_module, "start_job", lambda name, fn: fn())
    monkeypatch.setattr(pipeline, "run_extract", lambda log, full=False: seen.append(full))
    assert local.post("/api/extract", json={"full": True}, headers=GUI).json() == {"started": True}
    assert local.post("/api/extract", json={}, headers=GUI).json() == {"started": True}
    assert seen == [True, False]


@pytest.mark.parametrize("path,body", [
    ("/api/publish", None), ("/api/extract", {"full": True}),
    ("/api/candidates/1/approve", edited()), ("/api/candidates/1/reject", None),
    ("/api/candidates/1/reopen", None), ("/api/candidates/1/merge", {"into_id": 2}),
])
def test_all_writes_require_gui_header(path, body):
    assert local.post(path, json=body).status_code == 403


def test_merge_retarget_and_reopen_rebuild_already_extracted_signals(conn):
    a = candidate(conn, email="a@client.test", name="A")
    b = candidate(conn, email="b@client.test", name="B")
    c = candidate(conn, email="c@client.test", name="C")
    def detail(cid): return local.get(f"/api/candidates/{cid}").json()
    def emails(cid): return detail(cid)["fields"]["emails"]["value"]
    assert local.post(f"/api/candidates/{a}/merge", json={"into_id": b}, headers=GUI).json() == {"ok": True}
    assert emails(b) == ["a@client.test", "b@client.test"]
    assert detail(a)["review_state"] == "merged"
    assert local.post(f"/api/candidates/{a}/merge", json={"into_id": c}, headers=GUI).status_code == 200
    assert emails(b) == ["b@client.test"] and emails(c) == ["a@client.test", "c@client.test"]
    assert local.post(f"/api/candidates/{a}/reopen", headers=GUI).json() == {"ok": True}
    assert emails(a) == ["a@client.test"] and emails(c) == ["c@client.test"]
    assert detail(a)["merged_into"] is None
    assert conn.execute("SELECT count(*) AS n FROM raw_items WHERE extracted_at IS NULL").fetchone()["n"] == 0


def test_merge_does_not_overwrite_reviewed_target(conn):
    a = candidate(conn, email="a@client.test", name="A")
    b = candidate(conn, email="b@client.test", name="B")
    approve(b)
    before = local.get(f"/api/candidates/{b}").json()
    assert local.post(f"/api/candidates/{a}/merge", json={"into_id": b}, headers=GUI).status_code == 200
    after = local.get(f"/api/candidates/{b}").json()
    assert after["fields"] == before["fields"] and after["reviewed"] == before["reviewed"]


def test_approval_validates_stage_and_amounts_before_writing(conn):
    cid = candidate(conn)
    bad = edited()
    bad["stage"] = "unknown"
    assert approve(cid, bad).status_code == 422
    bad = edited()
    bad["amounts"][0]["amount"] = -1
    assert approve(cid, bad).status_code == 422
    assert conn.execute("SELECT review_state FROM candidates WHERE id=%s", (cid,)).fetchone()["review_state"] == "pending"


def test_approval_waiting_for_publish_remains_eligible_and_updates_same_client(conn, monkeypatch):
    from contextlib import contextmanager

    cid = candidate(conn)
    assert approve(cid).status_code == 200
    locked = threading.Event()
    approval_waiting = threading.Event()
    release = threading.Event()
    original = db.connect

    class Connection:
        def __init__(self, real):
            self.real = real

        def execute(self, query, args=None):
            if "FOR UPDATE" in query and "WHERE id =" in query:
                approval_waiting.set()
            cursor = self.real.execute(query, args)
            if "FOR UPDATE" in query and "WHERE review_state='approved'" in query:
                locked.set()
                assert release.wait(timeout=5)
            return cursor

    @contextmanager
    def connect():
        with original() as real:
            yield Connection(real)

    monkeypatch.setattr(db, "connect", connect)
    with ThreadPoolExecutor(2) as pool:
        publishing = pool.submit(publish)
        try:
            assert locked.wait(timeout=5)
            approving = pool.submit(approve, cid, edited("Concurrent approval"))
            assert approval_waiting.wait(timeout=5)
        finally:
            release.set()
        assert publishing.result(timeout=5).json()["published"] == 1
        assert approving.result(timeout=5).json() == {"ok": True}
    assert conn.execute("SELECT count(*) AS n FROM clients").fetchone()["n"] == 1
    assert local.get("/api/status").json()["ready_to_publish"] == 1
    assert publish().json() == {"published": 1, "inserted": 0, "updated": 1}
    assert conn.execute("SELECT name FROM clients").fetchone()["name"] == "Concurrent approval"


def test_reopen_without_signals_preserves_reviewed_and_published_identity(conn):
    cid = candidate(conn)
    approve(cid)
    publish()
    before = local.get(f"/api/candidates/{cid}").json()
    conn.execute("DELETE FROM raw_items")
    conn.commit()
    assert local.post(f"/api/candidates/{cid}/reopen", headers=GUI).json() == {"ok": True}
    after = local.get(f"/api/candidates/{cid}").json()
    assert after["review_state"] == "pending" and after["client_id"] == before["client_id"]
    assert after["reviewed"] == before["reviewed"]
    approve(cid, edited("Still same client"))
    assert publish().json() == {"published": 1, "inserted": 0, "updated": 1}
    assert conn.execute("SELECT count(*) AS n FROM clients").fetchone()["n"] == 1


def test_merged_candidate_must_be_reopened_before_approval(conn):
    a = candidate(conn, email="a@client.test")
    b = candidate(conn, email="b@client.test")
    assert local.post(f"/api/candidates/{a}/merge", json={"into_id": b}, headers=GUI).status_code == 200
    assert approve(a).status_code == 409
    row = local.get(f"/api/candidates/{a}").json()
    assert row["review_state"] == "merged" and row["merged_into"] == b
