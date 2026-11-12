from fastapi.testclient import TestClient

from scraper.app import app
from test_auth import authenticated_client

local = TestClient(app, base_url="http://localhost")


def test_root_points_to_nextjs_gui_on_localhost():
    r = local.get("/")
    assert r.status_code == 200 and "http://127.0.0.1:3000" in r.text


def test_other_hosts_rejected():
    assert TestClient(app, base_url="http://evil.test").get("/").status_code == 400


def test_post_without_gui_header_rejected():
    assert local.post("/api/extract").status_code == 403


from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
import runpy

import pytest
from psycopg import OperationalError
from psycopg.types.json import Jsonb

from scraper import auth, db, pipeline
from scraper import app as app_module

GUI = {"X-Local-GUI": "1"}


@pytest.mark.parametrize("origins", ["", "*", "https://ui.example.test, https://other.example.test"])
def test_configured_hosts_and_cors(monkeypatch, origins):
    monkeypatch.setenv("ALLOWED_HOSTS", " localhost, api.example.test ")
    monkeypatch.setenv("CORS_ORIGINS", origins)
    configured = runpy.run_module("scraper.app")["app"]
    client = TestClient(configured, base_url="http://api.example.test")
    assert client.get("/").status_code == 200
    assert client.get("/", headers={"Host": "evil.test"}).status_code == 400
    headers = {"Origin": "https://ui.example.test", "Access-Control-Request-Method": "POST",
               "Access-Control-Request-Headers": "content-type,x-local-gui"}
    response = client.options("/api/extract", headers=headers)
    if origins.startswith("https"):
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == headers["Origin"]
        assert response.headers["access-control-allow-credentials"] == "true"
        assert set(response.headers["access-control-allow-methods"].split(", ")) == {"GET", "POST", "PUT", "PATCH", "DELETE"}
        assert client.post("/api/extract", headers={"Origin": headers["Origin"]}).status_code == 403
        denied = client.options("/api/extract", headers={**headers, "Origin": "https://evil.test"})
        assert denied.status_code == 400 and "access-control-allow-origin" not in denied.headers
        actual = client.post("/api/extract", headers={**GUI, "Origin": headers["Origin"]})
        assert actual.status_code == 401 and actual.headers["access-control-allow-origin"] == headers["Origin"]
    else:
        assert response.status_code == 403 and "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("host,port,gui_port,expected", [
    (None, None, None, ("127.0.0.1", 8765)),
    ("0.0.0.0", "10000", "9000", ("0.0.0.0", 10000)),
    (None, None, "9000", ("127.0.0.1", 9000)),
])
def test_api_bind_settings(monkeypatch, host, port, gui_port, expected):
    import uvicorn

    for key, value in (("HOST", host), ("PORT", port), ("GUI_PORT", gui_port)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    settings = {}
    monkeypatch.setattr(uvicorn, "run", lambda application, **kwargs: settings.update(kwargs))
    runpy.run_module("scraper.app", run_name="__main__")
    assert (settings["host"], settings["port"]) == expected
    assert settings["proxy_headers"] is False


@pytest.fixture
def conn(pg_url, monkeypatch, authenticated_client):
    monkeypatch.setitem(globals(), "local", authenticated_client)
    monkeypatch.setenv("INTERNAL_DOMAINS", "acme.test")
    monkeypatch.setenv("DAILY_SCRAPE_AT", "")  # TestClient lifespans must not launch scheduled scrapes.
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


def permission_client(conn, routes, role="Custom"):
    rid = conn.execute("INSERT INTO roles(name,routes) VALUES (%s,%s) RETURNING id", (role, routes)).fetchone()["id"]
    user = conn.execute(
        "INSERT INTO users(email,name,password_hash,role_id) VALUES ('routes@test','Routes','unused',%s)"
        " RETURNING id", (rid,)).fetchone()
    conn.commit()
    return TestClient(app, base_url="http://localhost", cookies={auth.COOKIE: auth.sign_token(user)})


@pytest.mark.parametrize("denied", ["review", "scraping", "accounts"])
def test_every_route_denies_without_changes_or_job_launch(conn, monkeypatch, denied):
    cid = candidate(conn)
    jid = job_row(conn)
    item = conn.execute("SELECT id FROM raw_items LIMIT 1").fetchone()["id"]
    monkeypatch.setattr(app_module, "_launch_job", lambda *args: pytest.fail("Denied request launched a job"))
    requests = {
        "review": [
            ("GET", "/api/candidates", None), ("GET", f"/api/candidates/{cid}", None),
            ("GET", "/api/candidates/facets", None),
            ("POST", "/api/candidates/bulk", {"ids": [cid], "action": "approve"}),
            ("POST", f"/api/candidates/{cid}/approve", edited()),
            ("POST", f"/api/candidates/{cid}/reject", None),
            ("POST", f"/api/candidates/{cid}/reopen", None),
            ("POST", f"/api/candidates/{cid}/merge", {"target_id": cid}),
            ("POST", "/api/publish", None), ("GET", f"/api/raw/{item}", None),
            ("GET", "/api/rules", None),
        ],
        "scraping": [
            ("POST", "/api/scrape", {"sources": ["gmail"]}), ("POST", "/api/extract", {}),
            *[("POST", f"/api/jobs/{jid}/{action}", None) for action in ("resume", "pause", "continue", "stop")],
            ("GET", "/api/jobs/latest/mailboxes", None), ("GET", f"/api/jobs/{jid}/mailboxes", None),
            ("GET", f"/api/jobs/{jid}/log", None),
        ],
    }
    requests["accounts"] = [
        ("GET", "/api/roles", None), ("POST", "/api/roles", {"name": "Denied", "routes": ["accounts"]}),
        ("PATCH", "/api/roles/1", {"name": "Denied", "routes": ["accounts"]}), ("DELETE", "/api/roles/1", None),
        ("GET", "/api/users", None), ("POST", "/api/users", {"email": "denied@test.com", "name": "Denied", "password": "x", "role_id": 1}),
        ("PATCH", "/api/users/1", {"role_id": 2}),
    ]
    requests["scraping"] += [("GET", "/api/schedule", None), ("PUT", "/api/schedule", {"enabled": False, "interval_minutes": 15})]
    tables = ("roles", "users", "schedule", "candidates", "clients", "raw_items", "signals", "jobs", "job_log", "job_mailboxes")
    with permission_client(conn, [key for key in auth.ROUTES if key != denied]) as member:
        before = {table: conn.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall() for table in tables}
        for method, path, body in requests[denied]:
            response = member.request(method, path, headers=GUI, json=body)
            assert response.status_code == 403, path
            assert response.json() == {"detail": "You don't have access to this page"}, path
    for table, rows in before.items():
        assert conn.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall() == rows


@pytest.mark.parametrize("role,routes", [
    ("Review only", ["review"]), ("Scraping only", ["scraping"]),
    ("Custom developer", ["scraping", "review"]), ("Administrator", ["accounts"]),
    ("Accounting", ["masterlist"]), ("Custom owner", list(auth.ROUTES)),
])
def test_route_grants_without_admin_bypass_and_status_redaction(conn, monkeypatch, role, routes):
    cid = candidate(conn)
    jid = job_row(conn, status="failed", name="scrape", params={"mailboxes": ["private@test"]})
    conn.execute("UPDATE jobs SET error='private failure' WHERE id=%s", (jid,))
    conn.execute("INSERT INTO job_log(job_id,line) VALUES (%s,'private log')", (jid,))
    conn.commit()
    launched = []
    monkeypatch.setattr(app_module, "_launch_job", lambda *args: launched.append(args))
    with permission_client(conn, routes, role) as client:
        status = client.get("/api/status")
        assert status.status_code == 200
        status = status.json()
        assert status["candidates"]["pending"] == 1 and status["job_status"] == "failed"
        assert status["job_id"] == jid and status["running"] is False
        if "scraping" in routes:
            assert status["log"] == ["private log"]
            assert status["job_params"] == {"mailboxes": ["private@test"]}
            assert status["error"] == "private failure"
            assert client.get("/api/jobs/latest/mailboxes").status_code == 200
            assert client.post("/api/extract", headers=GUI, json={}).status_code == 200
            assert len(launched) == 1
        else:
            assert not {"log", "job_params", "error"} & status.keys()
            assert client.get("/api/jobs/latest/mailboxes").status_code == 403
            assert client.post("/api/extract", headers=GUI, json={}).status_code == 403
        if "review" in routes:
            assert client.get("/api/candidates").status_code == 200
            assert client.post(f"/api/candidates/{cid}/reject", headers=GUI).status_code == 200
            assert conn.execute("SELECT review_state FROM candidates WHERE id=%s", (cid,)).fetchone()["review_state"] == "rejected"
        else:
            assert client.get("/api/candidates").status_code == 403
            assert client.post(f"/api/candidates/{cid}/reject", headers=GUI).status_code == 403
        assert client.get("/api/users").status_code == (200 if "accounts" in routes else 403)


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
    with TestClient(app, base_url="http://localhost", cookies=local.cookies, raise_server_exceptions=False) as client:
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
    monkeypatch.setattr(app_module, "_launch_job", app_module._run_job)
    monkeypatch.setattr(pipeline, "run_extract", lambda log, full=False, **kwargs: seen.append(full))
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


def details_body():
    body = edited()
    body.update(payments=[
        {"date": "2026-10-05T00:00:00Z", "amount": 1000, "currency": "PHP", "basis": "manual", "raw_item_id": None},
        {"date": "2026-09-01T00:00:00Z", "amount": None, "currency": None, "basis": "unknown", "raw_item_id": 1}],
        services_availed=["Website"], handlers=[{"department": "Finance", "mailbox": "billing@acme.test",
            "first": "2026-01-01T00:00:00Z", "last": "2026-10-05T00:00:00Z", "messages": 3}],
        client_since="1900-01-01T00:00:00Z", timeline={"items": [{"snippet": "PRIVATE EMAIL"}]})
    return body


def insert_review_candidate(conn, email, values=None, state="pending"):
    return conn.execute(
        "INSERT INTO candidates(email_key,fields,review_state) VALUES (%s,%s,%s) RETURNING id",
        (email, Jsonb({key: {"value": value} for key, value in (values or {}).items()}), state)).fetchone()["id"]


def test_review_filters_each_and_combined_with_paging(conn):
    values = {"name": "Target", "department": "Sales", "status": "active", "stage": "paid",
              "handlers": [{"mailbox": "staff@acme.test"}], "services_availed": ["SEO"],
              "services": ["Legacy"], "phones": ["123"], "amounts": [{"amount": 10}],
              "last_contact": "2026-10-05T23:59:59Z"}
    a = insert_review_candidate(conn, "a@Example.TEST", values)
    b = insert_review_candidate(conn, "b@example.test", values)
    other = insert_review_candidate(conn, "other@notexample.test", {"phones": None, "amounts": None,
                                                                   "last_contact": "2026-10-06T00:00:00Z"})
    empty = insert_review_candidate(conn, "empty@example.test")
    insert_review_candidate(conn, "approved@example.test", values, "approved")
    conn.commit()
    filters = {"mailbox": "staff@acme.test", "service": "SEO", "domain": "@EXAMPLE.test",
               "has_phone": "true", "has_amount": "true", "date_from": "2026-10-05", "date_to": "2026-10-05"}
    for key, value in filters.items():
        response = local.get("/api/candidates", params={key: value})
        expected = {a, b, empty} if key == "domain" else {a, b, other} if key == "date_from" else {a, b}
        assert {row["id"] for row in response.json()} == expected
        assert int(response.headers["X-Total-Count"]) == len(expected)
    assert {r["id"] for r in local.get("/api/candidates?service=Legacy").json()} == {a, b}
    combined = {**filters, "q": "Target", "department": "Sales", "status": "active", "stage": "paid",
                "size": 1, "page": 2}
    response = local.get("/api/candidates", params=combined)
    assert response.status_code == 200 and response.headers["X-Total-Count"] == "2"
    assert [r["id"] for r in response.json()] == [b]
    assert local.get("/api/candidates", params={key: "" for key in filters}).headers["X-Total-Count"] == "4"
    assert local.get("/api/candidates?has_phone=false&has_amount=false").headers["X-Total-Count"] == "4"
    for params in ({"service": "SEO' OR 1=1 --"}, {"domain": "ample.test"}, {"mailbox": "unknown"},
                   {"date_from": "2026-10-07"}, {"date_to": "2026-10-04"}):
        assert local.get("/api/candidates", params=params).headers["X-Total-Count"] == "0"


@pytest.mark.parametrize("value", ["2026-02-30", "bad", "20261005", "2026-W41-1"])
@pytest.mark.parametrize("key", ["date_from", "date_to"])
def test_review_date_filter_rejects_invalid_date(conn, key, value):
    assert local.get("/api/candidates", params={key: value}).status_code == 400


def test_review_facets_distinct_sorted_nonempty_and_state_scoped(conn):
    for email, values, state in [
        ("a@test", {"department": "Sales", "handlers": [{"mailbox": "z@test"}, {"mailbox": "a@test"}],
                    "services": ["SEO", "Legacy", ""], "services_availed": ["SEO", "Design"]}, "pending"),
        ("b@test", {"department": "Accounts", "handlers": [{"mailbox": "a@test"}, {"mailbox": ""}],
                    "services": None, "services_availed": None}, "pending"),
        ("c@test", {"department": " ", "handlers": None}, "pending"),
        ("d@test", {"department": "Private", "services": ["Other"]}, "approved"),
    ]:
        insert_review_candidate(conn, email, values, state)
    conn.commit()
    assert local.get("/api/candidates/facets").json() == {
        "departments": ["Accounts", "Sales"], "mailboxes": ["a@test", "z@test"],
        "services": ["Design", "Legacy", "SEO"]}
    assert local.get("/api/candidates/facets?state=approved").json() == {
        "departments": ["Private"], "mailboxes": [], "services": ["Other"]}
    assert local.get("/api/candidates/facets?state=rejected").json() == {
        "departments": [], "mailboxes": [], "services": []}


@pytest.mark.parametrize("full", [False, True])
def test_bulk_approve_matches_single_untouched_draft(conn, full):
    body = details_body() if full else {}
    body.pop("notes", None)
    body.update(name="  Client  ", company="  Co  ", department="  Sales  ",
                emails=[" first@test ", "second@test"], services=[" SEO ", "Design"])
    a = insert_review_candidate(conn, "a@test", body)
    b = insert_review_candidate(conn, "b@test", body)
    conn.commit()
    untouched = {**body, "name": "Client", "company": "Co", "department": "Sales", "notes": None,
                 "emails": ["first@test", "second@test"], "services": ["SEO", "Design"],
                 "status": body.get("status", "inquiry")}
    assert approve(a, untouched).status_code == 200
    assert local.post("/api/candidates/bulk", headers=GUI,
                      json={"ids": [b], "action": "approve"}).json() == {"done": [b], "failed": []}
    single = local.get(f"/api/candidates/{a}").json()
    bulk = local.get(f"/api/candidates/{b}").json()
    assert bulk["reviewed"] == single["reviewed"]
    assert bulk["reviewed_by"] == single["reviewed_by"] and bulk["reviewed_at"]


def test_bulk_mixed_batch_failures_do_not_stop_later_records(conn):
    approved = insert_review_candidate(conn, "approved@test", state="approved")
    merged = insert_review_candidate(conn, "merged@test", state="merged")
    rejected = insert_review_candidate(conn, "rejected@test", state="rejected")
    invalid = insert_review_candidate(conn, "invalid@test", {"payments": details_body()["payments"]})
    db_failure = insert_review_candidate(conn, "dbfail@test")
    good = insert_review_candidate(conn, "good@test")
    conn.execute("ALTER TABLE candidates ADD CONSTRAINT injected_bulk_failure CHECK "
                 "(email_key <> 'dbfail@test' OR review_state <> 'approved')")
    conn.commit()
    bad = [approved, merged, rejected, 999999, invalid, db_failure]
    result = local.post("/api/candidates/bulk", headers=GUI,
                        json={"ids": [*bad, good], "action": "approve"})
    assert result.status_code == 200
    result = result.json()
    assert result["done"] == [good] and [f["id"] for f in result["failed"]] == bad
    assert all(f["error"] for f in result["failed"])
    for cid in (invalid, db_failure):
        row = local.get(f"/api/candidates/{cid}").json()
        assert row["review_state"] == "pending" and row["reviewed"] is None
    assert conn.execute("SELECT count(*) AS n FROM clients").fetchone()["n"] == 0


@pytest.mark.parametrize("malformed", [
    {"emails": [123]}, {"payments": [None]}, {"name": 123},
    {"payments": [{"amount": 10, "currency": 123}]},
    {"emails": False},
] + [{key: value} for key in ("emails", "phones", "services", "services_availed", "amounts", "handlers", "payments")
     for value in ("alice@example.test", {})])
def test_bulk_malformed_extracted_values_fail_only_that_record(conn, malformed):
    first = insert_review_candidate(conn, "first@test")
    invalid = insert_review_candidate(conn, "invalid@test", malformed)
    last = insert_review_candidate(conn, "last@test")
    conn.commit()
    response = local.post("/api/candidates/bulk", headers=GUI,
                          json={"ids": [first, invalid, last], "action": "approve"})
    assert response.status_code == 200
    result = response.json()
    assert result["done"] == [first, last]
    assert result["failed"] == [{"id": invalid, "error": "invalid extracted field shapes"}]
    for cid, state in ((first, "approved"), (invalid, "pending"), (last, "approved")):
        row = local.get(f"/api/candidates/{cid}").json()
        assert row["review_state"] == state
        assert (row["reviewed"] is None) == (cid == invalid)


def test_bulk_reject_matches_single_and_requires_gui_header(conn):
    a = insert_review_candidate(conn, "a@test")
    b = insert_review_candidate(conn, "b@test")
    conn.commit()
    body = {"ids": [b], "action": "reject"}
    assert local.post("/api/candidates/bulk", json=body).status_code == 403
    assert local.post(f"/api/candidates/{a}/reject", headers=GUI).status_code == 200
    assert local.post("/api/candidates/bulk", headers=GUI, json=body).json() == {"done": [b], "failed": []}
    for cid in (a, b):
        row = local.get(f"/api/candidates/{cid}").json()
        assert row["review_state"] == "rejected" and row["reviewed"] is None


@pytest.mark.parametrize("body", [
    {"ids": [], "action": "approve"}, {"ids": [1, 1], "action": "approve"},
    {"ids": list(range(501)), "action": "reject"}, {"ids": [1], "action": "publish"},
    {"ids": ["bad"], "action": "reject"},
])
def test_bulk_validates_body_before_processing(conn, body):
    assert local.post("/api/candidates/bulk", headers=GUI, json=body).status_code == 422


def test_publish_client_details_insert_and_update_computes_since_and_excludes_timeline(conn):
    cid = candidate(conn)
    body = details_body()
    assert approve(cid, body).status_code == 200
    reviewed = local.get(f"/api/candidates/{cid}").json()["reviewed"]
    assert datetime.fromisoformat(reviewed["client_since"]) == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert len(reviewed["payments"]) == 2 and reviewed["payments"][0]["raw_item_id"] is None
    assert reviewed["services_availed"] == ["Website"] and reviewed["handlers"][0]["messages"] == 3
    assert "timeline" not in reviewed
    assert publish().json() == {"published": 1, "inserted": 1, "updated": 0}
    row = conn.execute("SELECT * FROM clients").fetchone()
    assert row["payments"] == reviewed["payments"]
    assert row["client_since"] == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert row["services_availed"] == ["Website"] and row["departments"] == reviewed["handlers"]
    assert "timeline" not in row and "PRIVATE EMAIL" not in str(row)
    body.update(payments=[{"date": "2026-10-01T00:00:00Z", "amount": 2000, "currency": "USD", "basis": "manual", "raw_item_id": None}],
                services_availed=["SEO"], handlers=[])
    assert approve(cid, body).status_code == 200
    assert publish().json() == {"published": 1, "inserted": 0, "updated": 1}
    updated = conn.execute("SELECT * FROM clients").fetchone()
    assert updated["id"] == row["id"] and updated["payments"][0]["amount"] == 2000
    assert updated["client_since"] == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert updated["services_availed"] == ["SEO"] and updated["departments"] == []


def test_older_gui_omitted_fields_use_extracted_details_and_explicit_empty_lists_clear(conn):
    cid = candidate(conn)
    extracted = details_body()
    for key in ("payments", "services_availed", "handlers"):
        conn.execute("UPDATE candidates SET fields=jsonb_set(fields, %s, %s) WHERE id=%s",
                     ([key], Jsonb({"value": extracted[key], "source": "derived", "confidence": 1}), cid))
    conn.commit()
    assert approve(cid, edited()).status_code == 200
    reviewed = local.get(f"/api/candidates/{cid}").json()["reviewed"]
    assert len(reviewed["payments"]) == 2
    assert reviewed["services_availed"] == ["Website"] and reviewed["handlers"][0]["messages"] == 3
    body = edited()
    body.update(payments=[], services_availed=[], handlers=[])
    assert approve(cid, body).status_code == 200
    reviewed = local.get(f"/api/candidates/{cid}").json()["reviewed"]
    assert reviewed["payments"] == [] and reviewed["client_since"] is None
    assert reviewed["engagement_paid"] is True
    assert reviewed["services_availed"] == [] and reviewed["handlers"] == []


@pytest.mark.parametrize("changes", [
    {"amount": 0}, {"amount": -1}, {"amount": "NaN"}, {"amount": 1000, "currency": None},
    {"amount": None, "currency": "PHP"}, {"amount": None, "currency": None, "basis": "stated"},
    {"basis": "unknown"}, {"basis": "guess"}, {"currency": "123"}, {"currency": "US"},
])
def test_payment_validation_rejects_invalid_rows_before_review_is_saved(conn, changes):
    cid = candidate(conn)
    body = details_body()
    body["payments"][0].update(changes)
    assert approve(cid, body).status_code == 422
    assert conn.execute("SELECT reviewed FROM candidates WHERE id=%s", (cid,)).fetchone()["reviewed"] is None


def test_payments_require_engagement_paid_even_when_filled_from_extraction(conn):
    cid = candidate(conn)
    body = details_body()
    body["engagement_paid"] = False
    assert approve(cid, body).status_code == 422
    conn.execute("UPDATE candidates SET fields=jsonb_set(fields, '{payments}', %s) WHERE id=%s",
                 (Jsonb({"value": details_body()["payments"]}), cid))
    conn.commit()
    del body["payments"]
    assert approve(cid, body).status_code == 422


def test_handlers_validate_nonnegative_message_counts(conn):
    cid = candidate(conn)
    body = details_body()
    body["handlers"][0]["messages"] = -1
    assert approve(cid, body).status_code == 422


def test_legacy_reviewed_json_publishes_with_new_defaults(conn):
    cid = candidate(conn)
    conn.execute("UPDATE candidates SET review_state='approved', reviewed=%s, reviewed_at=now(), reviewed_by='legacy' WHERE id=%s",
                 (Jsonb(edited()), cid))
    conn.commit()
    assert publish().json()["inserted"] == 1
    row = conn.execute("SELECT payments, client_since, services_availed, departments FROM clients").fetchone()
    assert row == {"payments": [], "client_since": None, "services_availed": [], "departments": []}


def test_payment_dates_accept_mixed_naive_and_aware_values_and_ignore_client_since(conn):
    cid = candidate(conn)
    body = details_body()
    body["payments"][0]["date"] = "2026-08-01T00:00:00"
    body["client_since"] = "ignored input"
    assert approve(cid, body).status_code == 200
    reviewed = local.get(f"/api/candidates/{cid}").json()["reviewed"]
    assert datetime.fromisoformat(reviewed["client_since"]) == datetime(2026, 8, 1, tzinfo=timezone.utc)


def job_row(conn, status="running", name="extract", params=None, stale=False):
    jid = conn.execute("INSERT INTO jobs (name, params, status) VALUES (%s,%s,%s) RETURNING id",
                       (name, Jsonb(params or {"full": False}), status)).fetchone()["id"]
    if stale:
        conn.execute("UPDATE jobs SET heartbeat_at=now()-interval '3 minutes' WHERE id=%s", (jid,))
    conn.commit()
    return jid


def test_job_log_survives_work_rollback_and_app_restart(conn):
    jid = job_row(conn)
    before = conn.execute("SELECT heartbeat_at FROM jobs WHERE id=%s", (jid,)).fetchone()["heartbeat_at"]
    with pytest.raises(ValueError):
        with db.connect() as work:
            db.upsert_raw(work, "gmail", "a", "rollback", {})
            app_module.log(jid, "survives rollback")
            raise ValueError("rollback work")
    assert conn.execute("SELECT count(*) AS n FROM raw_items").fetchone()["n"] == 0
    assert conn.execute("SELECT heartbeat_at FROM jobs WHERE id=%s", (jid,)).fetchone()["heartbeat_at"] > before
    with TestClient(app, base_url="http://localhost", cookies=local.cookies) as restarted:
        state = restarted.get("/api/status").json()
        assert state["job_id"] == jid and state["job_status"] == "running"
        assert state["log"][-1].endswith("survives rollback")
    conn.execute("UPDATE jobs SET heartbeat_at=now()-interval '3 minutes'")
    conn.commit()
    with TestClient(app, base_url="http://localhost", cookies=local.cookies) as restarted:
        assert conn.execute("SELECT status FROM jobs WHERE id=%s", (jid,)).fetchone()["status"] == "interrupted"
        state = restarted.get("/api/status").json()
        assert state["job_status"] == "interrupted" and not state["running"]
        assert state["log"][-1].endswith("survives rollback")


def test_status_last_500_lines_oldest_first_latest_job_only(conn):
    old = job_row(conn, status="done")
    conn.execute("INSERT INTO job_log (job_id, line) VALUES (%s,'old log')", (old,))
    conn.commit()
    jid = job_row(conn, status="done")
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO job_log (job_id, line) VALUES (%s,%s)", [(jid, f"line {i}") for i in range(503)])
    conn.commit()
    with TestClient(app, base_url="http://localhost", cookies=local.cookies) as client:
        state = client.get("/api/status").json()
    assert state["log"] == [f"line {i}" for i in range(3, 503)]
    assert state["job_id"] == jid and state["job_params"] == {"full": False}
    assert state["job_failures"] == 0 and state["job_started_at"]
    assert state["job_status"] == "done" and state["job"] == "extract" and state["error"] is None


def test_status_marks_newly_stale_job_but_startup_preserves_fresh_remote_job(conn):
    jid = job_row(conn)
    with TestClient(app, base_url="http://localhost", cookies=local.cookies) as client:
        assert conn.execute("SELECT status FROM jobs").fetchone()["status"] == "running"
        conn.execute("UPDATE jobs SET heartbeat_at=now()-interval '3 minutes'")
        conn.commit()
        state = client.get("/api/status").json()
        assert state["job_id"] == jid and state["job_status"] == "interrupted" and state["job_finished_at"]


def test_duplicate_starts_across_connections_return_conflict(conn, monkeypatch):
    monkeypatch.setattr(app_module, "_launch_job", lambda *_: None, raising=False)
    def request():
        return local.post("/api/extract", json={"full": False}, headers=GUI).status_code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: request(), range(2))) == [200, 409]
    assert conn.execute("SELECT count(*) AS n FROM jobs WHERE status='running'").fetchone()["n"] == 1


@pytest.mark.parametrize("reset_done,want_full", [(False, True), (True, False)])
def test_resume_full_extract_claims_once_and_chooses_correct_reset_mode(conn, monkeypatch, reset_done, want_full):
    params = {"full": True}
    if reset_done:
        params["full_reset_done"] = True
    jid = job_row(conn, status="interrupted", params=params)
    launched = []
    monkeypatch.setattr(app_module, "_launch_job", lambda *args: launched.append(args), raising=False)
    response = local.post(f"/api/jobs/{jid}/resume", headers=GUI)
    assert response.status_code == 200
    rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    assert len(rows) == 2 and rows[0]["status"] == "done" and rows[1]["status"] == "running"
    assert rows[0]["error"] == f"resumed as job {rows[1]['id']}"
    assert rows[1]["params"]["full"] is want_full
    assert launched and launched[0][0] == rows[1]["id"]
    assert local.post(f"/api/jobs/{jid}/resume", headers=GUI).status_code == 400


def test_failed_job_can_be_resumed(conn, monkeypatch):
    params = {"sources": ["gmail"], "mailboxes": [], "since": "", "full": False, "done_mailboxes": ["a@acme.test"]}
    jid = job_row(conn, status="failed", name="scrape", params=params)
    monkeypatch.setattr(app_module, "_launch_job", lambda *_: None, raising=False)
    assert local.post(f"/api/jobs/{jid}/resume", headers=GUI).status_code == 200
    rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    assert rows[0]["status"] == "done" and rows[1]["params"] == params


def test_resume_while_running_keeps_interrupted_row_and_scrape_params(conn, monkeypatch):
    params = {"sources": ["gmail"], "mailboxes": ["sales@acme.test"], "since": "2020-01-01", "full": False}
    jid = job_row(conn, status="interrupted", name="scrape", params=params)
    active = job_row(conn)
    monkeypatch.setattr(app_module, "_launch_job", lambda *_: None, raising=False)
    assert local.post(f"/api/jobs/{jid}/resume", headers=GUI).status_code == 409
    assert conn.execute("SELECT status FROM jobs WHERE id=%s", (jid,)).fetchone()["status"] == "interrupted"
    conn.execute("UPDATE jobs SET status='done' WHERE id=%s", (active,))
    conn.commit()
    assert local.post(f"/api/jobs/{jid}/resume", headers=GUI).status_code == 200
    successor = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
    assert successor["name"] == "scrape" and successor["params"] == params
    assert local.post(f"/api/jobs/{jid}/resume").status_code == 403


@pytest.mark.parametrize("outcome", ["done", "failed", "done_with_errors"])
def test_runner_persists_completion_and_error_status(conn, monkeypatch, outcome):
    def runner(log, full=False, *, checkpoint=lambda: None):
        log("doing work")
        if outcome == "failed":
            raise ValueError("fixture failure")
        if outcome == "done_with_errors":
            log.failure(2)
    monkeypatch.setattr(pipeline, "run_extract", runner)
    monkeypatch.setattr(app_module, "_launch_job", app_module._run_job)
    assert local.post("/api/extract", json={}, headers=GUI).status_code == 200
    state = local.get("/api/status").json()
    assert state["job_status"] == outcome and state["running"] is False and state["job_finished_at"]
    assert state["job_failures"] == (2 if outcome == "done_with_errors" else 0)
    assert state["error"] == ("ValueError('fixture failure')" if outcome == "failed" else None)
    assert any(line.endswith("doing work") for line in state["log"])


def test_heartbeat_updates_without_log_output_while_runner_is_alive(conn, monkeypatch):
    import time

    started, release = threading.Event(), threading.Event()
    worker = []
    def runner(log, full=False, *, checkpoint=lambda: None):
        started.set()
        assert release.wait(timeout=5)
    def launch(*args):
        thread = threading.Thread(target=app_module._run_job, args=args)
        worker.append(thread)
        thread.start()
    monkeypatch.setattr(pipeline, "run_extract", runner)
    monkeypatch.setattr(app_module, "HEARTBEAT_SECONDS", .01, raising=False)
    monkeypatch.setattr(app_module, "_launch_job", launch, raising=False)
    try:
        assert local.post("/api/extract", json={}, headers=GUI).status_code == 200
        assert started.wait(timeout=5)
        initial = conn.execute("SELECT heartbeat_at FROM jobs").fetchone()["heartbeat_at"]
        deadline = time.monotonic() + 3
        while conn.execute("SELECT heartbeat_at FROM jobs").fetchone()["heartbeat_at"] <= initial:
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert local.get("/api/status").json()["running"] is True
    finally:
        release.set()
        for thread in worker:
            thread.join(timeout=5)
            assert not thread.is_alive()


def test_proxied_host_and_browser_header_pass_and_pointer_when_static_absent(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_launch_job", lambda *_: None, raising=False)
    proxied = TestClient(app, base_url="http://127.0.0.1:8765", cookies=local.cookies)
    proxied.cookies.set("scraper_session", local.cookies.get("scraper_session"), domain="127.0.0.1", path="/")
    assert proxied.post("/api/extract", json={}).status_code == 403
    assert proxied.post("/api/extract", json={}, headers=GUI).status_code == 200
    assert TestClient(app, base_url="http://foreign.test").get("/api/status").status_code == 400
    monkeypatch.setattr(app_module, "ROOT", tmp_path)
    response = proxied.get("/")
    assert response.status_code == 200 and "http://127.0.0.1:3000" in response.text


def test_concurrent_resume_claims_only_one_successor(conn, monkeypatch):
    jid = job_row(conn, status="interrupted")
    monkeypatch.setattr(app_module, "_launch_job", lambda *_: None)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: local.post(f"/api/jobs/{jid}/resume", headers=GUI).status_code, range(2)))
    assert sorted(results) == [200, 400]
    assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 2


@pytest.mark.parametrize("status,stale", [("done", False), ("done_with_errors", False), ("running", True)])
def test_resume_requires_interrupted_state_without_changing_other_states(conn, monkeypatch, status, stale):
    jid = job_row(conn, status=status, stale=stale)
    monkeypatch.setattr(app_module, "_launch_job", lambda *_: None)
    assert local.post(f"/api/jobs/{jid}/resume", headers=GUI).status_code == 400
    assert conn.execute("SELECT status FROM jobs WHERE id=%s", (jid,)).fetchone()["status"] == status


@pytest.mark.parametrize("error_type", [RuntimeError, OperationalError])
def test_startup_serves_gui_when_database_is_unavailable_and_status_retries(conn, monkeypatch, error_type):
    job_row(conn, stale=True)
    original = db.connect
    def unavailable():
        raise error_type("database unavailable")
    monkeypatch.setattr(db, "connect", unavailable)
    with TestClient(app, base_url="http://localhost", cookies=local.cookies, raise_server_exceptions=False) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/status").status_code == 500
        monkeypatch.setattr(db, "connect", original)
        assert client.get("/api/status").json()["job_status"] == "interrupted"


@pytest.mark.parametrize("error_type", [OperationalError, RuntimeError])
@pytest.mark.parametrize("operation,line", [("log", "fixture line"), ("failure", "failure(3)"), ("full_reset_done", "full_reset_done")])
def test_job_logger_connection_errors_fall_back_to_stderr(monkeypatch, capsys, error_type, operation, line):
    def unavailable():
        raise error_type("temporary database error")
    monkeypatch.setattr(db, "connect", unavailable)
    logger = app_module.JobLog(42)
    if operation == "log":
        result = logger("fixture line")
    elif operation == "failure":
        result = logger.failure(3)
    else:
        result = logger.full_reset_done()
    assert result is None
    assert capsys.readouterr().err.splitlines() == [
        f"job 42 log write failed: {error_type('temporary database error')!r}; line: {line}"]


def test_job_log_fallback_keeps_multiline_messages_on_one_stderr_line(monkeypatch, capsys):
    def unavailable():
        raise OperationalError("offline")
    monkeypatch.setattr(db, "connect", unavailable)
    app_module.log(42, "first\nsecond\rthird")
    assert capsys.readouterr().err.splitlines() == [
        "job 42 log write failed: OperationalError('offline'); line: first\\nsecond\\rthird"]


@pytest.fixture
def fail_job_write(monkeypatch):
    from contextlib import contextmanager

    original = db.connect
    def install(predicate):
        class Connection:
            def __init__(self, real): self.real = real
            @property
            def autocommit(self): return self.real.autocommit
            @autocommit.setter
            def autocommit(self, value): self.real.autocommit = value
            def execute(self, query, args=None):
                if predicate(query, args):
                    raise OperationalError("transient write failure")
                return self.real.execute(query, args)
            def __getattr__(self, name):  # commit, rollback, cursor... for pipeline connections
                return getattr(self.real, name)
        @contextmanager
        def connect():
            with original() as real:
                yield Connection(real)
        monkeypatch.setattr(db, "connect", connect)
    return install


@pytest.mark.parametrize("outcome", ["done", "done_with_errors", "failed"])
def test_logging_outage_preserves_runner_result_including_final_message(conn, monkeypatch, capsys, fail_job_write, outcome):
    jid = job_row(conn)
    completed = []
    def runner(logger, full=False, *, checkpoint=lambda: None):
        logger("before outage")
        logger("during outage")
        completed.append("continued")
        if outcome == "done_with_errors":
            logger.failure(2)
        if outcome == "failed":
            raise ValueError("real runner failure")
    fail_job_write(lambda query, args: query.startswith("INSERT INTO job_log") and (
        args[1].endswith("during outage") or args[1].endswith("extract: finished") or "ERROR:" in args[1]))
    monkeypatch.setattr(pipeline, "run_extract", runner)
    app_module._run_job(jid, "extract", {"full": False})
    assert completed == ["continued"]
    row = conn.execute("SELECT status, error, failures FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row == {"status": outcome, "error": "ValueError('real runner failure')" if outcome == "failed" else None,
                   "failures": 2 if outcome == "done_with_errors" else 0}
    persisted = conn.execute("SELECT line FROM job_log WHERE job_id=%s", (jid,)).fetchall()
    assert len(persisted) == 1 and persisted[0]["line"].endswith("before outage")
    stderr = capsys.readouterr().err.splitlines()
    assert len(stderr) == 2 and all(f"job {jid} log write failed:" in line for line in stderr)
    assert "line: during outage" in stderr[0]
    assert ("line: ERROR: ValueError('real runner failure')" if outcome == "failed" else "line: extract: finished") in stderr[1]


@pytest.mark.parametrize("operation,query_prefix,line", [
    ("failure", "UPDATE jobs SET failures=", "failure(3)"),
    ("full_reset_done", "UPDATE jobs SET params=", "full_reset_done"),
])
def test_job_metadata_write_errors_are_swallowed(conn, capsys, fail_job_write, operation, query_prefix, line):
    jid = job_row(conn, params={"full": True})
    fail_job_write(lambda query, args: query.startswith(query_prefix))
    logger = app_module.JobLog(jid)
    result = logger.failure(3) if operation == "failure" else logger.full_reset_done()
    assert result is None
    assert capsys.readouterr().err.splitlines() == [
        f"job {jid} log write failed: OperationalError('transient write failure'); line: {line}"]
    assert conn.execute("SELECT status, failures, params FROM jobs").fetchone() == {
        "status": "running", "failures": 0, "params": {"full": True}}


def test_job_log_heartbeat_write_error_does_not_undo_committed_line(conn, capsys, fail_job_write):
    jid = job_row(conn)
    fail_job_write(lambda query, args: query.startswith("UPDATE jobs SET heartbeat_at="))
    app_module.log(jid, "insert survived")
    assert conn.execute("SELECT line FROM job_log").fetchone()["line"].endswith("insert survived")
    assert capsys.readouterr().err.splitlines() == [
        f"job {jid} log write failed: OperationalError('transient write failure'); line: insert survived"]


@pytest.mark.parametrize("state,control,action,code,wanted", [
    ("running", None, "pause", 200, "pause"), ("running", "pause", "pause", 400, "pause"),
    ("running", "stop", "pause", 400, "stop"), ("paused", "pause", "pause", 400, "pause"),
    ("running", None, "continue", 400, None), ("running", "pause", "continue", 200, None),
    ("paused", "pause", "continue", 200, None), ("paused", None, "continue", 200, None),
    ("paused", "stop", "continue", 400, "stop"), ("running", "stop", "continue", 400, "stop"),
    ("running", None, "stop", 200, "stop"), ("running", "pause", "stop", 200, "stop"),
    ("paused", "pause", "stop", 200, "stop"), ("running", "stop", "stop", 400, "stop"),
    ("done", None, "pause", 400, None), ("stopped", None, "continue", 400, None),
    ("failed", None, "stop", 400, None),
])
def test_job_control_transitions_are_atomic_and_stop_wins(conn, state, control, action, code, wanted):
    jid = job_row(conn, status=state)
    conn.execute("UPDATE jobs SET control=%s WHERE id=%s", (control, jid))
    conn.commit()
    response = local.post(f"/api/jobs/{jid}/{action}", headers=GUI)
    assert response.status_code == code
    assert response.json() == {"ok": True} if code == 200 else "detail" in response.json()
    assert conn.execute("SELECT status, control FROM jobs WHERE id=%s", (jid,)).fetchone() == {"status": state, "control": wanted}
    assert local.post(f"/api/jobs/{jid}/{action}").status_code == 403


def test_paused_job_blocks_start_and_heartbeats_and_stales_like_running(conn, monkeypatch):
    jid = job_row(conn, status="paused")
    conn.execute("UPDATE jobs SET control='pause' WHERE id=%s", (jid,))
    conn.commit()
    state = local.get("/api/status").json()
    assert state["running"] is True and state["job_status"] == "paused" and state["job_control"] == "pause"
    assert local.post("/api/extract", json={}, headers=GUI).status_code == 409
    before = conn.execute("SELECT heartbeat_at FROM jobs").fetchone()["heartbeat_at"]
    class StopAfterOne:
        calls = 0
        def wait(self, seconds):
            self.calls += 1
            return self.calls > 1
    app_module._heartbeat(jid, StopAfterOne())
    assert conn.execute("SELECT heartbeat_at FROM jobs").fetchone()["heartbeat_at"] > before
    conn.execute("UPDATE jobs SET heartbeat_at=now()-interval '3 minutes'")
    conn.commit()
    state = local.get("/api/status").json()
    assert state["job_status"] == "interrupted" and state["job_control"] is None and not state["running"]


@pytest.mark.parametrize("status", ["stopped", "interrupted"])
def test_resume_maps_known_params_and_keeps_done_mailbox_lineage(conn, monkeypatch, status):
    params = {"sources": ["gmail"], "mailboxes": ["a", "b"], "since": "2020-01-01", "full": False,
              "done_mailboxes": ["a"], "irrelevant_metadata": True, "full_reset_done": True}
    jid = job_row(conn, status=status, name="scrape", params=params)
    seen = []
    def runner(sources, mailboxes, since, full, log, *, checkpoint, done_mailboxes):
        seen.append((sources, mailboxes, since, full, done_mailboxes))
        checkpoint()
    monkeypatch.setattr(pipeline, "run_scrape", runner)
    monkeypatch.setattr(app_module, "_launch_job", app_module._run_job)
    assert local.post(f"/api/jobs/{jid}/resume", headers=GUI).status_code == 200
    assert seen == [(["gmail"], ["a", "b"], "2020-01-01", False, ["a"])]
    assert local.post(f"/api/jobs/{jid}/resume", headers=GUI).status_code == 400


def test_late_stop_wins_terminal_transition_and_never_logs_finished(conn, monkeypatch):
    jid = job_row(conn)
    def runner(log, full=False, *, checkpoint):
        with db.connect() as control_conn:
            control_conn.execute("UPDATE jobs SET control='stop' WHERE id=%s", (jid,))
    monkeypatch.setattr(pipeline, "run_extract", runner)
    app_module._run_job(jid, "extract", {"full": False})
    assert conn.execute("SELECT status, control FROM jobs").fetchone() == {"status": "stopped", "control": None}
    lines = [r["line"] for r in conn.execute("SELECT line FROM job_log")]
    assert any(line.endswith("stopped") for line in lines)
    assert not any("finished" in line for line in lines)


def test_mailbox_board_order_latest_and_logs(conn):
    assert local.get('/api/jobs/latest/mailboxes').json()['job_id'] is None
    assert local.get('/api/jobs/latest/mailboxes').json()['synced_before'] == 0
    jid = job_row(conn, status='done', name='scrape')
    logger = app_module.JobLog(jid)
    logger.mailboxes(['z', 'a', 'b', 'c', 'd', 'z'], ['d'])
    logger.mailbox_status('z', 'running')
    logger.mailbox_status('a', 'failed', "ValueError('first')")
    logger.mailbox_status('a', 'failed', 'second')
    conn.execute("UPDATE job_mailboxes SET status='done' WHERE mailbox='c'")
    db.set_cursor(conn, 'gmail', 'a', '1')
    db.set_cursor(conn, 'gmail', 'c', '2')
    db.set_cursor(conn, 'contacts', 'b', '3')
    synced = datetime(2026, 1, 2, tzinfo=timezone.utc)
    conn.execute("UPDATE sync_state SET updated_at=%s", (synced,))
    conn.commit()
    job_row(conn, status='done', name='extract')
    board = local.get(f'/api/jobs/{jid}/mailboxes').json()
    assert [r['mailbox'] for r in board['items']] == ['z', 'a', 'b', 'c', 'd']
    assert board['counts'] == dict.fromkeys(['pending', 'running', 'done', 'failed', 'skipped'], 1)
    assert board['items'][0]['started_at'] and board['items'][1]['finished_at']
    assert board['items'][1]['error'] == "ValueError('first')"
    assert board['synced_before'] == 3  # failed with Gmail history, done, skipped; counted once each
    assert board['job_id'] == jid
    assert [datetime.fromisoformat(r['last_synced']) if r['last_synced'] else None
            for r in board['items']] == [None, synced, None, synced, None]
    assert local.get('/api/jobs/latest/mailboxes').json() == {'job_id': jid, **board}
    # A resume creates a new scrape job; the board follows it.
    resumed = job_row(conn, status='running', name='scrape')
    assert local.get('/api/jobs/latest/mailboxes').json()['job_id'] == resumed
    conn.execute("UPDATE jobs SET status='done' WHERE id=%s", (resumed,))
    conn.commit()
    with conn.cursor() as cur:
        cur.executemany('INSERT INTO job_log(job_id,line) VALUES (%s,%s)',
                        [(jid, f'{i} a%_@test') for i in range(1005)] + [(jid, 'unrelated')])
    conn.commit()
    assert len(local.get(f'/api/jobs/{jid}/log').json()['lines']) == 1000
    assert local.get(f'/api/jobs/{jid}/log', params={'mailbox': 'a%_@test', 'limit': 2}).json() == {
        'lines': ['1003 a%_@test', '1004 a%_@test']}
    assert local.get(f'/api/jobs/{jid}/log', params={'mailbox': 'aXX@test'}).json() == {'lines': []}
    assert len(local.get(f'/api/jobs/{jid}/log?limit=5000').json()['lines']) == 1006
    for limit in (0, -1, 5001):
        assert local.get(f'/api/jobs/{jid}/log?limit={limit}').status_code == 422


@pytest.mark.parametrize('overlap', [False, True])
def test_scrape_carries_union_and_seeds_skipped_board(conn, monkeypatch, overlap):
    mailboxes = [f'm{i}@test' for i in range(92)]
    params = {'sources': ['gmail'], 'mailboxes': mailboxes, 'since': '2020-01-01', 'full': False}
    first = mailboxes[:59]
    second = mailboxes[58:62] if overlap else mailboxes[59:63]
    old_ids = [job_row(conn, status=status, name='scrape', params={**params, 'done_mailboxes': done})
               for status, done in [('interrupted', first), ('failed', second)]]
    conn.execute("UPDATE jobs SET control='stop' WHERE id=ANY(%s)", (old_ids,))
    conn.commit()
    monkeypatch.setenv('SCRAPE_SINCE', params['since'])
    monkeypatch.setattr(app_module, '_launch_job', lambda jid, name, p:
                        app_module.JobLog(jid).mailboxes(p['mailboxes'], p.get('done_mailboxes', [])))
    response = local.post('/api/scrape', headers=GUI, json={
        'sources': ['gmail'], 'mailboxes': ['  ' + m.upper() + ' ' for m in mailboxes] + [' '], 'since': ''})
    expected = sorted(set(first + second))
    assert response.status_code == 200 and response.json() == {'started': True, 'carried': len(expected)}
    new = conn.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 1').fetchone()
    assert new['params'] == {**params, 'done_mailboxes': expected}
    old = conn.execute('SELECT status,control,error FROM jobs WHERE id=ANY(%s)', (old_ids,)).fetchall()
    assert old == [{'status': 'done', 'control': None, 'error': f"continued as job {new['id']}"}] * 2
    board = local.get('/api/jobs/latest/mailboxes').json()
    assert board['counts']['skipped'] == board['synced_before'] == len(expected)
    assert board['counts']['pending'] == 92 - len(expected)
    assert sorted(r['mailbox'] for r in board['items'] if r['status'] == 'skipped') == expected


@pytest.mark.parametrize('key,value', [('since', '2019-01-01'), ('sources', ['contacts']), ('mailboxes', ['other@test'])])
def test_scrape_carry_skips_mismatched_params(conn, monkeypatch, key, value):
    params = {'sources': ['gmail'], 'mailboxes': ['a@test'], 'since': '2020-01-01', 'full': False}
    matched = job_row(conn, status='stopped', name='scrape', params={**params, 'done_mailboxes': ['a@test']})
    mismatch = job_row(conn, status='failed', name='scrape', params={**params, key: value, 'done_mailboxes': ['other@test']})
    monkeypatch.setattr(app_module, '_launch_job', lambda *args: None)
    assert local.post('/api/scrape', headers=GUI, json=params).json()['carried'] == 1
    assert conn.execute('SELECT status FROM jobs WHERE id=%s', (matched,)).fetchone()['status'] == 'done'
    assert conn.execute('SELECT status FROM jobs WHERE id=%s', (mismatch,)).fetchone()['status'] == 'failed'


@pytest.mark.parametrize('status,error', [('done', None), ('done_with_errors', None), ('done', 'resumed as job 99')])
def test_scrape_carry_stops_at_completed_scrape_even_with_other_params(conn, monkeypatch, status, error):
    params = {'sources': ['gmail'], 'mailboxes': ['a@test', 'b@test'], 'since': '2020-01-01'}
    old = job_row(conn, status='interrupted', name='scrape', params={**params, 'done_mailboxes': ['a@test']})
    boundary = job_row(conn, status=status, name='scrape', params={'sources': ['contacts']})
    conn.execute('UPDATE jobs SET error=%s WHERE id=%s', (error, boundary))
    conn.commit()
    recent = job_row(conn, status='stopped', name='scrape', params={**params, 'done_mailboxes': ['b@test']})
    job_row(conn, status='done', name='extract')  # other job kinds do not end the chain
    monkeypatch.setattr(app_module, '_launch_job', lambda *args: None)
    assert local.post('/api/scrape', headers=GUI, json=params).json()['carried'] == 1
    latest = conn.execute('SELECT params FROM jobs ORDER BY id DESC LIMIT 1').fetchone()['params']
    assert latest['done_mailboxes'] == ['b@test']
    assert conn.execute('SELECT status FROM jobs WHERE id=%s', (old,)).fetchone()['status'] == 'interrupted'
    assert conn.execute('SELECT error FROM jobs WHERE id=%s', (recent,)).fetchone()['error'].startswith('continued as job ')
    assert conn.execute('SELECT error FROM jobs WHERE id=%s', (boundary,)).fetchone()['error'] == error


@pytest.mark.parametrize('flags', [{}, {'full': True}, {'fresh': True}, {'full': True, 'fresh': True}])
def test_scrape_without_chain_or_with_explicit_reset_carries_nothing(conn, monkeypatch, flags):
    params = {'sources': ['gmail'], 'mailboxes': ['a@test'], 'since': '2020-01-01'}
    if flags:
        old = job_row(conn, status='stopped', name='scrape', params={**params, 'done_mailboxes': ['a@test']})
    monkeypatch.setattr(app_module, '_launch_job', lambda *args: None)
    assert local.post('/api/scrape', headers=GUI, json={**params, **flags}).json() == {'started': True, 'carried': 0}
    latest = conn.execute('SELECT params FROM jobs ORDER BY id DESC LIMIT 1').fetchone()['params']
    assert latest == {**params, 'full': flags.get('full', False)}
    if flags:
        assert conn.execute('SELECT status FROM jobs WHERE id=%s', (old,)).fetchone()['status'] == 'stopped'


@pytest.mark.parametrize('status', ['running', 'paused'])
def test_scrape_carry_conflict_preserves_old_jobs(conn, monkeypatch, status):
    params = {'sources': ['gmail'], 'mailboxes': ['a@test'], 'since': '2020-01-01'}
    old = job_row(conn, status='stopped', name='scrape', params={**params, 'done_mailboxes': ['a@test']})
    job_row(conn, status=status, name='extract')
    monkeypatch.setattr(app_module, '_launch_job', lambda *args: pytest.fail('must not launch'))
    assert local.post('/api/scrape', headers=GUI, json=params).status_code == 409
    assert conn.execute('SELECT count(*) AS n FROM jobs').fetchone()['n'] == 2
    assert conn.execute('SELECT status,error FROM jobs WHERE id=%s', (old,)).fetchone() == {'status': 'stopped', 'error': None}


def test_scrape_carry_marking_failure_rolls_back_new_job(conn, monkeypatch, fail_job_write):
    params = {'sources': ['gmail'], 'mailboxes': ['a@test'], 'since': '2020-01-01'}
    old = job_row(conn, status='stopped', name='scrape', params={**params, 'done_mailboxes': ['a@test']})
    fail_job_write(lambda query, args: query.startswith("UPDATE jobs SET status='done', control=NULL"))
    monkeypatch.setattr(app_module, '_launch_job', lambda *args: pytest.fail('must not launch'))
    with pytest.raises(OperationalError, match='transient write failure'):
        local.post('/api/scrape', headers=GUI, json=params)
    assert conn.execute('SELECT count(*) AS n FROM jobs').fetchone()['n'] == 1
    assert conn.execute('SELECT status,error FROM jobs WHERE id=%s', (old,)).fetchone() == {'status': 'stopped', 'error': None}


@pytest.mark.parametrize('full', [False, True])
@pytest.mark.parametrize('overlap', [False, True])
def test_resume_scrape_merges_older_union_and_preserves_full(conn, monkeypatch, full, overlap):
    mailboxes = [f'm{i}@test' for i in range(92)]
    params = {'sources': ['gmail'], 'mailboxes': mailboxes, 'since': '2020-01-01', 'full': full}
    first = mailboxes[:59]
    last = mailboxes[58:62] if overlap else mailboxes[59:63]
    older = [job_row(conn, status=status, name='scrape', params={**params, 'done_mailboxes': done})
             for status, done in [('interrupted', first), ('stopped', first[:1])]]
    target = job_row(conn, status='failed', name='scrape', params={**params, 'done_mailboxes': last})
    conn.execute("UPDATE jobs SET control='stop' WHERE id=ANY(%s)", (older + [target],))
    conn.commit()
    launched = []
    monkeypatch.setattr(app_module, '_launch_job', lambda *args: launched.append(args))
    response = local.post(f'/api/jobs/{target}/resume', headers=GUI)
    new = conn.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 1').fetchone()
    expected = {**params, 'done_mailboxes': sorted(set(first + last))}
    assert response.status_code == 200 and response.json() == {'started': True, 'job_id': new['id']}
    assert new['params'] == expected
    assert launched == [(new['id'], 'scrape', expected)]
    for jid in older + [target]:
        marker = 'resumed' if jid == target else 'continued'
        assert conn.execute('SELECT status,control,error FROM jobs WHERE id=%s', (jid,)).fetchone() == {
            'status': 'done', 'control': None, 'error': f"{marker} as job {new['id']}"}
    assert local.post(f'/api/jobs/{target}/resume', headers=GUI).status_code == 400


@pytest.mark.parametrize('key,value', [('since', '2019-01-01'), ('sources', ['contacts']),
                                       ('mailboxes', ['other@test']), ('full', True)])
def test_resume_scrape_excludes_other_settings_and_job_kinds(conn, monkeypatch, key, value):
    params = {'sources': ['gmail'], 'mailboxes': ['a@test', 'b@test'], 'since': '2020-01-01', 'full': False}
    ignored = [job_row(conn, status='stopped', name=name, params={**params, **changes, 'done_mailboxes': ['a@test']})
               for name, changes in [('scrape', {key: value}), ('extract', {})]]
    target = job_row(conn, status='interrupted', name='scrape', params={**params, 'done_mailboxes': ['b@test']})
    monkeypatch.setattr(app_module, '_launch_job', lambda *args: None)
    assert local.post(f'/api/jobs/{target}/resume', headers=GUI).status_code == 200
    assert conn.execute('SELECT params FROM jobs ORDER BY id DESC LIMIT 1').fetchone()['params'] == {
        **params, 'done_mailboxes': ['b@test']}
    assert [r['status'] for r in conn.execute('SELECT status FROM jobs WHERE id=ANY(%s)', (ignored,))] == ['stopped'] * 2


@pytest.mark.parametrize('status,error', [('done', None), ('done_with_errors', None), ('done', 'resumed as job 99')])
def test_resume_scrape_chain_respects_completed_boundary_and_target_id(conn, monkeypatch, status, error):
    params = {'sources': ['gmail'], 'mailboxes': [], 'since': '2020-01-01', 'full': False}
    before = job_row(conn, status='stopped', name='scrape', params={**params, 'done_mailboxes': ['before']})
    boundary = job_row(conn, status=status, name='scrape', params={'sources': ['contacts']})
    conn.execute('UPDATE jobs SET error=%s WHERE id=%s', (error, boundary))
    conn.commit()
    older = job_row(conn, status='failed', name='scrape', params={**params, 'done_mailboxes': ['older']})
    target = job_row(conn, status='stopped', name='scrape', params={**params, 'done_mailboxes': ['target']})
    newer = job_row(conn, status='interrupted', name='scrape', params={**params, 'done_mailboxes': ['newer']})
    job_row(conn, status='done', name='extract')
    monkeypatch.setattr(app_module, '_launch_job', lambda *args: None)
    assert local.post(f'/api/jobs/{target}/resume', headers=GUI).status_code == 200
    assert conn.execute('SELECT params FROM jobs ORDER BY id DESC LIMIT 1').fetchone()['params']['done_mailboxes'] == ['older', 'target']
    assert conn.execute('SELECT status FROM jobs WHERE id=%s', (before,)).fetchone()['status'] == 'stopped'
    assert conn.execute('SELECT status FROM jobs WHERE id=%s', (newer,)).fetchone()['status'] == 'interrupted'
    assert conn.execute('SELECT error FROM jobs WHERE id=%s', (boundary,)).fetchone()['error'] == error
    assert conn.execute('SELECT error FROM jobs WHERE id=%s', (older,)).fetchone()['error'].startswith('continued as job ')


@pytest.mark.parametrize('marker', ['resumed', 'continued'])
def test_resume_scrape_chain_markers_roll_back_with_successor(conn, monkeypatch, fail_job_write, marker):
    params = {'sources': ['gmail'], 'mailboxes': [], 'since': '2020-01-01', 'full': False}
    older = job_row(conn, status='stopped', name='scrape', params={**params, 'done_mailboxes': ['a']})
    target = job_row(conn, status='interrupted', name='scrape', params={**params, 'done_mailboxes': ['b']})
    fail_job_write(lambda query, args: query.startswith("UPDATE jobs SET status='done', control=NULL")
                   and args[0].startswith(marker))
    monkeypatch.setattr(app_module, '_launch_job', lambda *args: pytest.fail('must not launch'))
    with pytest.raises(OperationalError, match='transient write failure'):
        local.post(f'/api/jobs/{target}/resume', headers=GUI)
    rows = conn.execute('SELECT id,status,error FROM jobs ORDER BY id').fetchall()
    assert rows == [{'id': older, 'status': 'stopped', 'error': None},
                    {'id': target, 'status': 'interrupted', 'error': None}]


def test_concurrent_scrape_resume_merges_chain_only_once(conn, monkeypatch):
    params = {'sources': ['gmail'], 'mailboxes': [], 'since': '2020-01-01', 'full': False}
    job_row(conn, status='stopped', name='scrape', params={**params, 'done_mailboxes': ['a']})
    target = job_row(conn, status='interrupted', name='scrape', params={**params, 'done_mailboxes': ['b']})
    monkeypatch.setattr(app_module, '_launch_job', lambda *args: None)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: local.post(f'/api/jobs/{target}/resume', headers=GUI).status_code, range(2)))
    assert sorted(results) == [200, 400]
    assert conn.execute('SELECT count(*) AS n FROM jobs').fetchone()['n'] == 3
    assert conn.execute('SELECT params FROM jobs ORDER BY id DESC LIMIT 1').fetchone()['params']['done_mailboxes'] == ['a', 'b']


def test_candidate_counts_merge_chain_shared_items_and_cap(conn):
    ids = [candidate(conn, email=f'{key}@client.test') for key in ['a', 'b', 'c']]
    # A shared signal must count once, even across a multi-hop merge.
    raw = conn.execute("SELECT raw_item_id FROM signals WHERE key='a@client.test'").fetchone()['raw_item_id']
    conn.execute("INSERT INTO signals(raw_item_id,key,data) VALUES (%s,'b@client.test','{}')", (raw,))
    conn.execute("UPDATE candidates SET review_state='merged',merged_into=%s WHERE id=%s", (ids[1], ids[0]))
    conn.execute("UPDATE candidates SET review_state='merged',merged_into=%s WHERE id=%s", (ids[2], ids[1]))
    conn.execute("UPDATE candidates SET fields=jsonb_set(fields,'{first_contact}',%s) WHERE id=%s",
                 (Jsonb({'value': '2020-01-01T00:00:00Z'}), ids[2]))
    with conn.cursor() as cur:
        cur.executemany('INSERT INTO candidates(email_key) VALUES (%s)', [(f'zero{i}@test',) for i in range(501)])
    conn.commit()
    response = local.get('/api/candidates')
    assert response.headers['X-Total-Count'] == '502' and len(response.json()) == 500
    first = [r['id'] for r in response.json()]
    response = local.get('/api/candidates?page=2')
    assert response.headers['X-Total-Count'] == '502' and len(response.json()) == 2
    assert not {r['id'] for r in response.json()} & set(first)
    pages = [local.get('/api/candidates', params={'page': p, 'size': 50}).json() for p in (1, 2)]
    assert [r['id'] for r in pages[0] + pages[1]] == first[:100]
    for bad in ({'page': 0}, {'size': 0}, {'size': 501}):
        assert local.get('/api/candidates', params=bad).status_code == 422
    response = local.get('/api/candidates?q=c@client')
    assert response.headers['X-Total-Count'] == '1'
    row = response.json()[0]
    assert row['items'] == 3 and row['first_contact'] == '2020-01-01T00:00:00Z'
    response = local.get('/api/candidates?q=zero0')
    assert response.json()[0]['items'] == 0 and response.json()[0]['first_contact'] is None
    response = local.get('/api/candidates?status=impossible')
    assert response.json() == [] and response.headers['X-Total-Count'] == '0'


def test_progress_write_failure_never_fails_scrape(conn, monkeypatch, fail_job_write, capsys):
    fail_job_write(lambda query, args: 'job_mailboxes' in query)
    monkeypatch.setattr(pipeline.gmail, 'fetch_mailbox', lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module, '_launch_job', app_module._run_job)
    jid = app_module.start_job('scrape', {'sources': ['gmail'], 'mailboxes': ['a'], 'since': '2020-01-01', 'full': False})
    job = conn.execute('SELECT status, error FROM jobs WHERE id=%s', (jid,)).fetchone()
    assert (job['status'], job['error']) == ('done', None)
    assert 'mailbox write failed' in capsys.readouterr().err


def test_list_scraped_details_latest_amount_and_company_search(conn):
    amounts = [
        {'amount': 100, 'currency': 'PHP', 'stage': 'quoted', 'date': '2026-03-01T00:00:00Z', 'raw_item_id': 99},
        {'amount': 999, 'currency': 'USD', 'stage': 'quoted', 'date': None},
        {'amount': 200, 'currency': 'PHP', 'stage': 'invoiced', 'date': '2026-05-01T00:00:00Z'},
        {'amount': 50, 'currency': 'PHP', 'stage': 'quoted', 'date': '2026-01-01T00:00:00Z'},
    ]
    fields = {key: {'value': value} for key, value in {
        'company': 'Unique Company', 'phones': ['09175551234'], 'services': ['Legacy'],
        'services_availed': ['SEO', 'Design'], 'amounts': amounts,
    }.items()}
    conn.execute("INSERT INTO candidates(email_key,fields) VALUES ('details@client.test',%s)", (Jsonb(fields),))
    conn.commit()
    response = local.get('/api/candidates', params={'q': 'unique company'})
    assert response.headers['X-Total-Count'] == '1'
    row = response.json()[0]
    assert row['company'] == 'Unique Company' and row['phones'] == ['09175551234']
    assert row['services'] == ['SEO', 'Design']
    assert row['latest_amount'] == amounts[2]
    assert row['item_sources'] == {'gmail': 0, 'contacts': 0, 'sheets': 0} and row['items'] == 0
    assert local.get('/api/candidates?q=unique&stage=paid').headers['X-Total-Count'] == '0'


@pytest.mark.parametrize('fields,services,latest', [
    ({}, [], None),
    ({'company': None, 'phones': None, 'services_availed': None, 'services': None, 'amounts': None}, [], None),
    ({'phones': [], 'services_availed': [], 'amounts': []}, [], None),
    ({'services': ['Legacy']}, ['Legacy'], None),
    ({'services_availed': [], 'services': ['Legacy']}, ['Legacy'], None),
    ({'services_availed': None, 'services': ['Legacy']}, ['Legacy'], None),
    ({'amounts': [{'amount': 10, 'currency': 'USD', 'stage': 'quoted', 'date': None, 'raw_item_id': 1}]},
     [], {'amount': 10, 'currency': 'USD', 'stage': 'quoted', 'date': None}),
])
def test_list_scraped_details_empty_and_fallback(conn, fields, services, latest):
    conn.execute("INSERT INTO candidates(email_key,fields) VALUES ('empty@client.test',%s)",
                 (Jsonb({key: {'value': value} for key, value in fields.items()}),))
    conn.commit()
    row = local.get('/api/candidates').json()[0]
    assert row['company'] is None and row['phones'] == []
    assert row['services'] == services and row['latest_amount'] == latest


def test_list_item_sources_deduplicate_across_merge_chain(conn):
    ids = [conn.execute('INSERT INTO candidates(email_key) VALUES (%s) RETURNING id',
                        (f'{key}@client.test',)).fetchone()['id'] for key in ('a', 'b', 'c')]
    conn.execute("UPDATE candidates SET review_state='merged',merged_into=%s WHERE id=%s", (ids[1], ids[0]))
    conn.execute("UPDATE candidates SET review_state='merged',merged_into=%s WHERE id=%s", (ids[2], ids[1]))
    for source, keys in [('gmail', ['a', 'b', 'c']), ('contacts', ['a', 'c']), ('sheets', ['b'])]:
        db.upsert_raw(conn, source, '', source, {})
        raw_id = conn.execute('SELECT id FROM raw_items WHERE source=%s', (source,)).fetchone()['id']
        with conn.cursor() as cur:
            cur.executemany('INSERT INTO signals(raw_item_id,key,data) VALUES (%s,%s,%s)',
                            [(raw_id, f'{key}@client.test', Jsonb({})) for key in keys])
    conn.commit()
    response = local.get('/api/candidates')
    row = response.json()[0]
    assert response.headers['X-Total-Count'] == '1' and row['id'] == ids[2]
    assert row['item_sources'] == {'gmail': 1, 'contacts': 1, 'sheets': 1}
    assert row['items'] == sum(row['item_sources'].values()) == 3


@pytest.mark.parametrize('sort,order,positions', [
    ('first_contact', 'asc', [0, 1, 2, 3, 4]),
    ('first_contact', 'desc', [1, 2, 0, 3, 4]),
    ('last_contact', 'asc', [1, 2, 0, 3, 4]),
    ('last_contact', 'desc', [0, 1, 2, 3, 4]),
])
def test_contact_sort_directions_nulls_ties_and_default(conn, sort, order, positions):
    ids = []
    for i, (first, last) in enumerate([
        ('2020-01-01', '2022-01-01'), ('2021-01-01', '2020-01-01'),
        ('2021-01-01', '2020-01-01'), (None, None), (None, None),
    ]):
        fields = {'first_contact': {'value': first}, 'last_contact': {'value': last}}
        ids.append(conn.execute('INSERT INTO candidates(email_key,fields) VALUES (%s,%s) RETURNING id',
                                (f'sort{i}@test', Jsonb(fields))).fetchone()['id'])
    conn.commit()
    response = local.get('/api/candidates', params={'sort': sort, 'order': order})
    assert response.status_code == 200 and response.headers['X-Total-Count'] == '5'
    assert [row['id'] for row in response.json()] == [ids[i] for i in positions]
    assert [row['id'] for row in local.get('/api/candidates').json()] == ids


@pytest.mark.parametrize('sort', ['first_contact', 'last_contact'])
def test_contact_sort_selects_oldest_before_cap(conn, sort):
    from datetime import timedelta

    with conn.cursor() as cur:
        cur.executemany('INSERT INTO candidates(email_key,fields) VALUES (%s,%s)', [
            (f'cap{i}@test', Jsonb({sort: {'value': (datetime(2020, 1, 1) + timedelta(days=501-i)).isoformat()}}))
            for i in range(502)
        ])
    conn.commit()
    ids = [row['id'] for row in conn.execute('SELECT id FROM candidates ORDER BY id DESC')]
    response = local.get('/api/candidates', params={'sort': sort, 'order': 'asc'})
    assert response.headers['X-Total-Count'] == '502'
    assert [row['id'] for row in response.json()] == ids[:500]
    assert all(row['items'] == 0 and row['item_sources'] == {'gmail': 0, 'contacts': 0, 'sheets': 0}
               for row in response.json())


@pytest.mark.parametrize('params', [
    {'sort': 'name'}, {'order': 'sideways'}, {'sort': ''}, {'order': ''},
    {'sort': "last_contact' DESC; DROP TABLE candidates; --"}, {'order': 'ASC'},
])
def test_contact_sort_rejects_invalid_parameters(conn, params):
    assert local.get('/api/candidates', params=params).status_code == 400
