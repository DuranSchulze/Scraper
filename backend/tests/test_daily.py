"""Interval scheduling uses fake time and disposable Postgres, never Google APIs."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import logging
import threading

import pytest

from scraper import app as api
from scraper import db, pipeline
from test_auth import authenticated_client


class Checks:
    def __init__(self, count=1, after_check=lambda: None):
        self.count = count
        self.after_check = after_check

    def is_set(self):
        return self.count <= 0

    def wait(self, seconds):
        assert seconds == 60
        self.count -= 1
        self.after_check()


@pytest.fixture
def scheduled(pg_url, monkeypatch):
    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO schedule(id,enabled,interval_minutes) VALUES (1,true,15)")
    monkeypatch.setenv("SCRAPE_SINCE", "2018-01-01")
    now = [datetime.now(timezone.utc)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0] if tz is None else now[0].astimezone(tz)

    monkeypatch.setattr(api, "datetime", Clock)
    launched = []

    def launch(jid, name, params):
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET started_at=%s WHERE id=%s", (now[0], jid))
        launched.append((jid, name, params))

    monkeypatch.setattr(api, "_launch_job", launch)
    return now, launched


@pytest.mark.parametrize("enabled,running,age,expected", [
    (False, False, None, False), (True, True, None, False),
    (True, False, None, True), (True, False, 14, False),
    (True, False, 15, True), (True, False, 16, True),
])
def test_auto_decision(enabled, running, age, expected):
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    last = now - timedelta(minutes=age) if age is not None else None
    assert api._auto_due(now, enabled, 15, last, running) is expected


@pytest.mark.parametrize("outcome", ["done", "done_with_errors", "failed", "stopped"])
def test_interval_even_after_failure_and_restart(scheduled, outcome):
    now, launched = scheduled

    def finish():
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET status=%s,finished_at=%s", (outcome, now[0]))

    api._auto_scrape(Checks(2, finish))
    api._auto_scrape(Checks())
    assert len(launched) == 1 and launched[0][1] == "scrape"
    assert launched[0][2] == {
        "sources": ["gmail", "contacts", "sheets"], "mailboxes": [],
        "since": "2018-01-01", "full": False, "auto": True,
    }
    now[0] += timedelta(minutes=15)
    api._auto_scrape(Checks())
    assert len(launched) == 2


@pytest.mark.parametrize("status", ["running", "paused"])
def test_busy_job_retried_next_minute(scheduled, status):
    now, launched = scheduled
    with db.connect() as conn:
        conn.execute("INSERT INTO jobs(name,status) VALUES ('extract',%s)", (status,))

    def finish():
        if not launched:
            with db.connect() as conn:
                conn.execute("UPDATE jobs SET status='done',finished_at=%s", (now[0],))

    api._auto_scrape(Checks(2, finish))
    assert len(launched) == 1


def test_schedule_reread_and_manual_jobs_do_not_reset_interval(scheduled, monkeypatch):
    _, launched = scheduled
    monkeypatch.setenv("SCRAPE_SINCE", "2020-01-01")
    with db.connect() as conn:
        conn.execute("UPDATE schedule SET enabled=false")
        conn.execute("INSERT INTO jobs(name,status) VALUES ('scrape','done')")
        conn.execute("INSERT INTO jobs(name,status,params) VALUES ('scrape','stopped',%s)",
                     (api.Jsonb({"sources": ["gmail", "contacts", "sheets"], "mailboxes": [],
                                 "since": "2020-01-01", "full": False, "done_mailboxes": ["a@test"]}),))

    def enable():
        with db.connect() as conn:
            conn.execute("UPDATE schedule SET enabled=true,interval_minutes=5")

    api._auto_scrape(Checks(2, enable))
    assert len(launched) == 1
    assert launched[0][2]["since"] == "2020-01-01"
    assert launched[0][2]["done_mailboxes"] == ["a@test"]


@pytest.mark.parametrize("since", [None, "", " \t "])
def test_default_since_when_unset_or_blank(scheduled, monkeypatch, since):
    _, launched = scheduled
    if since is None:
        monkeypatch.delenv("SCRAPE_SINCE", raising=False)
    else:
        monkeypatch.setenv("SCRAPE_SINCE", since)
    api._auto_scrape(Checks())
    assert len(launched) == 1 and launched[0][2]["since"] == "2018-01-01"


def test_two_scheduler_workers_only_start_once(scheduled, monkeypatch):
    _, launched = scheduled
    launch = api._launch_job

    def finish_immediately(jid, name, params):
        launch(jid, name, params)
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET status='done',finished_at=now() WHERE id=%s", (jid,))

    monkeypatch.setattr(api, "_launch_job", finish_immediately)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: api._auto_scrape(Checks()), range(2)))
    assert len(launched) == 1


@pytest.mark.parametrize("error", [RuntimeError("database unavailable"), api.PostgresError("offline")])
def test_database_failure_does_not_kill_scheduler(scheduled, monkeypatch, error, caplog):
    _, launched = scheduled
    connect = db.connect
    attempts = []

    def flaky():
        attempts.append(True)
        if len(attempts) == 1:
            raise error
        return connect()

    monkeypatch.setattr(db, "connect", flaky)
    api._auto_scrape(Checks(2))
    assert len(launched) == 1 and "retrying in 60 seconds" in caplog.text


def test_manual_start_race_is_retried(scheduled, monkeypatch, caplog):
    _, launched = scheduled
    caplog.set_level(logging.INFO)
    start = api.start_job
    attempts = []

    def conflict_once(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise api.HTTPException(409, "another job is still running")
        return start(*args, **kwargs)

    monkeypatch.setattr(api, "start_job", conflict_once)
    api._auto_scrape(Checks(2))
    assert len(launched) == 1 and len(attempts) == 2
    assert "another job is still running" in caplog.text


def test_scrape_already_chains_incremental_extract(scheduled, monkeypatch):
    _, launched = scheduled
    extracted = []
    monkeypatch.setattr(pipeline, "run_extract", lambda *args, **kwargs: extracted.append(kwargs.get("full", False)))
    # Empty sources exercise the real scrape -> extract chain without accessing external services.
    params = {"sources": [], "mailboxes": [], "since": "2018-01-01", "full": False}
    jid = api.start_job("scrape", params)
    api._run_job(jid, "scrape", params)
    with db.connect() as conn:
        job = conn.execute("SELECT status,error FROM jobs WHERE id=%s", (jid,)).fetchone()
        assert job["status"] == "done", job
    assert extracted == [False] and len(launched) == 1


@pytest.mark.parametrize("value,enabled,interval", [
    ("", False, 15), ("bad", False, 15), ("4", False, 15), ("1441", False, 15),
    ("5", True, 5), ("15", True, 15), ("1440", True, 1440),
])
def test_lifespan_seed_daemon_and_shutdown(scheduled, monkeypatch, value, enabled, interval, caplog):
    monkeypatch.setenv("SCRAPE_EVERY_MINUTES", value)
    monkeypatch.setenv("DAILY_SCRAPE_AT", "09:00")
    monkeypatch.setattr(api, "jwt_secret", lambda: None)
    monkeypatch.setattr(api.auth, "seed_admin", lambda conn: None)
    with db.connect() as conn:
        conn.execute("DELETE FROM schedule")
    threads = []

    class Thread:
        def __init__(self, *, target, args, daemon):
            assert target is api._auto_scrape and daemon is True
            self.stopped = args[0]
            threads.append(self)

        def start(self):
            assert not self.stopped.is_set()

        def join(self, timeout):
            assert self.stopped.is_set() and timeout == 1

    monkeypatch.setattr(api.threading, "Thread", Thread)

    async def boot():
        async with api.lifespan(api.app):
            with db.connect() as conn:
                assert conn.execute("SELECT enabled,interval_minutes FROM schedule").fetchone() == {
                    "enabled": enabled, "interval_minutes": interval}

    asyncio.run(boot())
    assert len(threads) == 1 and caplog.text.count("DAILY_SCRAPE_AT is ignored") == 1
    monkeypatch.setenv("SCRAPE_EVERY_MINUTES", "30")
    asyncio.run(boot())  # persisted settings win over later environment changes


def test_stopped_scheduler_does_not_access_database(monkeypatch):
    stopped = threading.Event()
    stopped.set()
    monkeypatch.setattr(db, "connect", lambda: pytest.fail("Stopped scheduler accessed database"))
    api._auto_scrape(stopped)


def test_schedule_constraints(scheduled):
    from psycopg.errors import CheckViolation, NotNullViolation, UniqueViolation

    with db.connect() as conn:
        for sql in ("INSERT INTO schedule VALUES (2,true,15,now())",
                    "INSERT INTO schedule VALUES (1,true,15,now())",
                    "UPDATE schedule SET interval_minutes=4",
                    "UPDATE schedule SET interval_minutes=1441",
                    "UPDATE schedule SET enabled=NULL"):
            with pytest.raises((CheckViolation, NotNullViolation, UniqueViolation)):
                with conn.transaction():
                    conn.execute(sql)


def test_schedule_api(scheduled, authenticated_client):
    now, _ = scheduled
    client = authenticated_client
    response = client.get("/api/schedule")
    assert response.status_code == 200
    assert response.json() == {"enabled": True, "interval_minutes": 15,
                               "last_auto_at": None, "next_run_at": now[0].isoformat()}
    body = {"enabled": False, "interval_minutes": 30}
    assert client.put("/api/schedule", json=body).status_code == 403
    saved = client.put("/api/schedule", json=body, headers={"X-Local-GUI": "1"})
    assert saved.status_code == 200 and saved.json() == {**body, "last_auto_at": None, "next_run_at": None}
    assert client.get("/api/schedule").json() == saved.json()
    for invalid in (4, 1441, 5.5, "bad", None):
        assert client.put("/api/schedule", json={**body, "interval_minutes": invalid},
                          headers={"X-Local-GUI": "1"}).status_code == 422
    for interval in (5, 1440):
        response = client.put("/api/schedule", json={"enabled": True, "interval_minutes": interval},
                              headers={"X-Local-GUI": "1"})
        assert response.status_code == 200 and response.json()["interval_minutes"] == interval
        assert response.json()["next_run_at"] == now[0].isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE users SET role='member',routes=ARRAY['review']")
    assert client.get("/api/schedule").status_code == 403
    assert client.put("/api/schedule", json=body, headers={"X-Local-GUI": "1"}).status_code == 403
    client.cookies.clear()
    assert client.get("/api/schedule").status_code == 401
    assert client.put("/api/schedule", json=body, headers={"X-Local-GUI": "1"}).status_code == 401


@pytest.mark.parametrize("enabled,running,age,next_age", [
    (False, False, 20, None), (True, False, 5, 10),
    (True, False, 20, -5), (True, True, 20, None), (True, True, 5, 10),
])
def test_schedule_next_run(scheduled, enabled, running, age, next_age):
    now, _ = scheduled
    last = now[0] - timedelta(minutes=age)
    with db.connect() as conn:
        conn.execute("UPDATE schedule SET enabled=%s", (enabled,))
        conn.execute("INSERT INTO jobs(name,status,started_at,params) VALUES ('scrape','failed',%s,%s)",
                     (last, api.Jsonb({"auto": True})))
        conn.execute("INSERT INTO jobs(name,status) VALUES ('scrape','done')")
        if running:
            conn.execute("INSERT INTO jobs(name,status) VALUES ('extract','paused')")
    result = api.get_schedule()
    assert result["last_auto_at"] == last
    assert result["next_run_at"] == (now[0] + timedelta(minutes=next_age) if next_age is not None else None)


def test_changed_interval_applies_next_tick(scheduled):
    now, launched = scheduled
    with db.connect() as conn:
        conn.execute("INSERT INTO jobs(name,status,started_at,params) VALUES ('scrape','failed',%s,%s)",
                     (now[0] - timedelta(minutes=5), api.Jsonb({"auto": True})))

    def shorten():
        assert not launched
        with db.connect() as conn:
            conn.execute("UPDATE schedule SET interval_minutes=5")

    api._auto_scrape(Checks(1, shorten))
    api._auto_scrape(Checks())
    assert len(launched) == 1
