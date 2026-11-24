import csv
import io
from datetime import datetime, timezone

from openpyxl import load_workbook
from psycopg.types.json import Jsonb

from scraper import db
from test_auth import EMAIL, GUI, authenticated_client  # noqa: F401  (fixture)

RANGE = {"from": "2026-11-23", "to": "2026-11-24"}


def seed(conn):
    # 23:30 UTC on Nov 23 is 07:30 on Nov 24 in Manila; 15:00 UTC on Nov 22 is outside the range.
    for sid, at in (("in", "2026-11-23T23:30:00Z"), ("early", "2026-11-23T01:00:00Z"), ("out", "2026-11-22T15:00:00Z")):
        conn.execute("INSERT INTO raw_items (source,mailbox,source_id,payload,fetched_at) VALUES ('gmail','a',%s,'{}',%s)", (sid, at))
    job = conn.execute(
        "INSERT INTO jobs (name,status,params,completed,started_at,finished_at) VALUES"
        " ('scrape','done',%s,true,'2026-11-23T23:00:00Z','2026-11-23T23:40:00Z') RETURNING id",
        (Jsonb({"auto": True, "full": False}),)).fetchone()["id"]
    conn.execute("INSERT INTO job_mailboxes (job_id,mailbox,status,fetched) VALUES (%s,'a','done',2)", (job,))
    conn.execute("INSERT INTO candidates (email_key,fields,review_state,first_published_at,published_at)"
                 " VALUES ('ana@client.test',%s,'approved','2026-11-23T23:50:00Z','2026-11-23T23:50:00Z')",
                 (Jsonb({"name": {"value": "Ana"}, "company": {"value": "Acme"}}),))
    conn.commit()
    return job


def test_summary_jobs_daily_published_use_manila_days(authenticated_client):
    client = authenticated_client
    with db.connect() as conn:
        job = seed(conn)
    s = client.get("/api/logs/summary", params=RANGE).json()
    assert s["fetched"] == {"gmail": 2}
    assert s["runs"]["total"] == 1 and s["runs"]["auto"] == 1 and s["runs"]["completed"] == 1
    assert s["published"] == 1
    assert datetime.fromisoformat(s["last_complete_scrape_at"]) == datetime(2026, 11, 23, 23, 40, tzinfo=timezone.utc)

    jobs = client.get("/api/logs/jobs", params=RANGE)
    assert jobs.headers["X-Total-Count"] == "1"
    row = jobs.json()[0]
    assert {k: row[k] for k in ("id", "emails_fetched", "mailboxes_done", "seconds")} == \
        {"id": job, "emails_fetched": 2, "mailboxes_done": 1, "seconds": 2400}

    days = {d["day"]: d for d in client.get("/api/logs/daily", params=RANGE).json()}
    assert list(days) == ["2026-11-24", "2026-11-23"]
    assert days["2026-11-24"]["emails"] == 1 and days["2026-11-24"]["runs"] == 1 and days["2026-11-24"]["published"] == 1
    assert days["2026-11-23"]["emails"] == 1

    published = client.get("/api/logs/published", params=RANGE).json()
    assert [(p["email"], p["name"], p["company"]) for p in published] == [("ana@client.test", "Ana", "Acme")]


def test_csv_and_xlsx_exports(authenticated_client):
    client = authenticated_client
    with db.connect() as conn:
        seed(conn)
    res = client.get("/api/logs/export", params={**RANGE, "table": "jobs", "format": "csv"})
    assert res.status_code == 200 and "attachment" in res.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(res.content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["Job", "Type", "Trigger"]
    assert rows[1][2] == "Auto" and rows[1][6] == "2026-11-24 07:00"  # started 23:00 UTC is 07:00 PHT

    res = client.get("/api/logs/export", params={**RANGE, "table": "all", "format": "xlsx"})
    book = load_workbook(io.BytesIO(res.content))
    assert book.sheetnames == ["Summary", "Scrape runs", "Daily totals", "Added to directory"]
    assert book["Added to directory"]["C2"].value == "ana@client.test"

    assert client.get("/api/logs/export", params={**RANGE, "table": "all", "format": "csv"}).status_code == 422
    assert client.get("/api/logs/summary", params={"from": "2026-11-25", "to": "2026-11-24"}).status_code == 422


def test_logs_require_scraping_grant(authenticated_client):
    client = authenticated_client
    with db.connect() as conn:
        role = conn.execute("INSERT INTO roles (name,routes) VALUES ('Reviewer',ARRAY['review']) RETURNING id").fetchone()["id"]
        conn.execute("UPDATE users SET role_id=%s WHERE email=%s", (role, EMAIL))
    assert client.get("/api/logs/summary").status_code == 403
    assert client.get("/api/logs/export", params={"table": "jobs"}).status_code == 403


def test_republish_keeps_first_added_date_and_records_events(authenticated_client):
    client = authenticated_client
    reviewed = {"name": "Ana", "emails": ["ana@client.test"], "stage": "inquiry", "status": "inquiry"}
    with db.connect() as conn:
        conn.execute("INSERT INTO candidates (email_key,review_state,reviewed,reviewed_at)"
                     " VALUES ('ana@client.test','approved',%s,now())", (Jsonb(reviewed),))
    assert client.post("/api/publish", headers=GUI).json()["inserted"] == 1
    with db.connect() as conn:
        first = conn.execute("SELECT first_published_at FROM candidates").fetchone()["first_published_at"]
        conn.execute("UPDATE candidates SET reviewed_at=clock_timestamp()")
    assert client.post("/api/publish", headers=GUI).json()["updated"] == 1
    with db.connect() as conn:
        row = conn.execute("SELECT first_published_at, published_at FROM candidates").fetchone()
        events = conn.execute("SELECT by_user, inserted, updated FROM publish_events ORDER BY id").fetchall()
    assert row["first_published_at"] == first < row["published_at"]
    assert events == [{"by_user": EMAIL, "inserted": 1, "updated": 0}, {"by_user": EMAIL, "inserted": 0, "updated": 1}]
