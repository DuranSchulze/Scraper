"""Daily scheduling uses fake time and disposable Postgres, never Google APIs."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
import logging
import subprocess
import sys
import threading

import pytest

from scraper import app as api
from scraper import db, pipeline


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
    monkeypatch.setenv("DAILY_SCRAPE_AT", "")
    monkeypatch.setenv("SCRAPE_SINCE", "2018-01-01")
    now = [datetime(2026, 11, 11, 10).astimezone()]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0] if tz is None else now[0].astimezone(tz)

    monkeypatch.setattr(api, "datetime", Clock)
    launched = []
    monkeypatch.setattr(api, "_launch_job", lambda *args: launched.append(args))
    return now, launched


@pytest.mark.parametrize("running,last_date,age,boot,hour,expected", [
    (False, None, 2, False, 9, True),
    (False, None, 2, False, 8, False),
    (True, None, 25, True, 9, False),
    (False, "today", 25, True, 9, False),
    (False, "yesterday", 2, False, 9, True),
    (False, None, 25, True, 8, True),
    (False, None, None, True, 8, True),
    (False, None, 24, True, 8, False),
    (False, None, 2, True, 8, False),
    (False, None, 25, False, 8, False),
])
def test_daily_decision(running, last_date, age, boot, hour, expected):
    now = datetime(2026, 11, 11, hour, tzinfo=timezone.utc)
    last_date = now.date() if last_date == "today" else (now - timedelta(days=1)).date() if last_date else None
    last_done = now - timedelta(hours=age) if age is not None else None
    assert api._daily_due(now, last_date, last_done, running, time(9), boot=boot) is expected


@pytest.mark.parametrize("value", [None, "", "24:00", "12:60", "9:00", "09:0", "bad", "09:00:00"])
def test_off_and_invalid_configuration(value, monkeypatch, caplog):
    if value is None:
        monkeypatch.delenv("DAILY_SCRAPE_AT", raising=False)
    else:
        monkeypatch.setenv("DAILY_SCRAPE_AT", value)
    assert api._daily_time() is None
    assert ("daily scrape disabled" in caplog.text) == bool(value)


@pytest.mark.parametrize("value", ["00:00", "09:30", "23:59"])
def test_valid_configuration(value, monkeypatch):
    monkeypatch.setenv("DAILY_SCRAPE_AT", value)
    assert api._daily_time().strftime("%H:%M") == value


@pytest.mark.parametrize("outcome", ["done", "done_with_errors", "failed", "stopped"])
def test_once_per_local_day_even_after_restart(scheduled, outcome, caplog):
    now, launched = scheduled
    caplog.set_level(logging.INFO)

    def finish():
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET status=%s,finished_at=%s", (outcome, now[0]))

    api._daily_scrape(time(9), Checks(2, finish))
    api._daily_scrape(time(9), Checks())  # another API boot/worker sees today's persisted marker
    assert len(launched) == 1 and launched[0][1] == "scrape"
    assert launched[0][2] == {
        "sources": ["gmail", "contacts", "sheets"], "mailboxes": [], "since": "2018-01-01",
        "full": False, "daily_date": "2026-11-11",
    }
    assert "Daily scrape started" in caplog.text
    now[0] += timedelta(days=1)
    api._daily_scrape(time(9), Checks())
    assert len(launched) == 2 and launched[1][2]["daily_date"] == "2026-11-12"


@pytest.mark.parametrize("status", ["running", "paused"])
def test_busy_job_retried_next_minute(scheduled, status, caplog):
    now, launched = scheduled
    caplog.set_level(logging.INFO)
    with db.connect() as conn:
        conn.execute("INSERT INTO jobs(name,status) VALUES ('extract',%s)", (status,))

    def finish():
        if not launched:
            with db.connect() as conn:
                conn.execute("UPDATE jobs SET status='done',finished_at=%s", (now[0],))

    api._daily_scrape(time(9), Checks(2, finish))
    assert len(launched) == 1
    assert "retrying in 60 seconds" in caplog.text


def test_busy_job_retry_survives_midnight(scheduled):
    now, launched = scheduled
    now[0] = now[0].replace(hour=23, minute=59)
    with db.connect() as conn:
        conn.execute("INSERT INTO jobs(name,status,finished_at) VALUES ('scrape','done',%s)",
                     (now[0] - timedelta(hours=1),))
        conn.execute("INSERT INTO jobs(name,status) VALUES ('extract','running')")

    def next_minute():
        now[0] += timedelta(minutes=1)
        if not launched:
            with db.connect() as conn:
                conn.execute("UPDATE jobs SET status='done' WHERE name='extract'")

    api._daily_scrape(time(23, 59), Checks(2, next_minute))
    assert len(launched) == 1 and launched[0][2]["daily_date"] == "2026-11-12"


@pytest.mark.parametrize("age,expected", [(None, 1), (25, 1), (24, 0), (2, 0)])
def test_boot_catch_up_before_scheduled_time(scheduled, age, expected):
    now, launched = scheduled
    if age is not None:
        with db.connect() as conn:
            conn.execute("INSERT INTO jobs(name,status,finished_at) VALUES ('scrape','done',%s)",
                         (now[0] - timedelta(hours=age),))
    api._daily_scrape(time(23), Checks())
    assert len(launched) == expected


def test_future_time_waits_then_uses_configured_since_and_carry(scheduled, monkeypatch):
    now, launched = scheduled
    monkeypatch.setenv("SCRAPE_SINCE", "2020-01-01")
    with db.connect() as conn:
        conn.execute("INSERT INTO jobs(name,status,finished_at) VALUES ('scrape','done',%s)", (now[0],))
        conn.execute("INSERT INTO jobs(name,status,params) VALUES ('scrape','stopped',%s)",
                     (api.Jsonb({"sources": ["gmail", "contacts", "sheets"], "mailboxes": [],
                                 "since": "2020-01-01", "full": False, "done_mailboxes": ["a@test"]}),))
    api._daily_scrape(time(11), Checks(2, lambda: now.__setitem__(0, now[0] + timedelta(hours=1))))
    assert len(launched) == 1
    assert launched[0][2]["since"] == "2020-01-01" and launched[0][2]["full"] is False
    assert launched[0][2]["done_mailboxes"] == ["a@test"]


@pytest.mark.parametrize("since", [None, "", " \t "])
def test_default_since_when_unset_or_blank(scheduled, monkeypatch, since):
    _, launched = scheduled
    if since is None:
        monkeypatch.delenv("SCRAPE_SINCE", raising=False)
    else:
        monkeypatch.setenv("SCRAPE_SINCE", since)
    api._daily_scrape(time(9), Checks())
    assert len(launched) == 1 and launched[0][2]["since"] == "2018-01-01"


def test_start_and_skip_reach_default_uvicorn_console(scheduled):
    # A subprocess keeps production logging separate from pytest's capture configuration.
    script = """
import logging
from datetime import time
import uvicorn
from scraper import app as api

uvicorn.Config(api.app)
assert logging.getLogger().getEffectiveLevel() == logging.WARNING
api._launch_job = lambda *args: None

class Checks:
    remaining = 2
    def is_set(self):
        return self.remaining == 0
    def wait(self, seconds):
        assert seconds == 60
        self.remaining -= 1

api._daily_scrape(time(0), Checks())
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "Daily scrape started: job" in result.stderr
    assert "Daily scrape skipped: another job is running" in result.stderr


def test_two_scheduler_workers_only_start_once(scheduled, monkeypatch):
    _, launched = scheduled
    launch = api._launch_job

    def finish_immediately(jid, name, params):
        launch(jid, name, params)
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET status='done',finished_at=now() WHERE id=%s", (jid,))

    monkeypatch.setattr(api, "_launch_job", finish_immediately)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: api._daily_scrape(time(9), Checks()), range(2)))
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
    api._daily_scrape(time(9), Checks(2))
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
    api._daily_scrape(time(9), Checks(2))
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


@pytest.mark.parametrize("value,starts", [("", False), ("bad", False), ("09:00", True)])
def test_lifespan_starts_daemon_and_stops_on_shutdown(scheduled, monkeypatch, value, starts):
    monkeypatch.setenv("DAILY_SCRAPE_AT", value)
    monkeypatch.setattr(api, "jwt_secret", lambda: None)
    monkeypatch.setattr(api.auth, "seed_admin", lambda conn: None)
    threads = []

    class Thread:
        def __init__(self, *, target, args, daemon):
            assert target is api._daily_scrape and daemon is True
            self.stopped = args[1]
            threads.append(self)

        def start(self):
            assert not self.stopped.is_set()

        def join(self, timeout):
            assert self.stopped.is_set() and timeout == 1

    monkeypatch.setattr(api.threading, "Thread", Thread)

    async def boot():
        async with api.lifespan(api.app):
            assert bool(threads) == starts

    asyncio.run(boot())


def test_stopped_scheduler_does_not_access_database(monkeypatch):
    stopped = threading.Event()
    stopped.set()
    monkeypatch.setattr(db, "connect", lambda: pytest.fail("Stopped scheduler accessed database"))
    api._daily_scrape(time(9), stopped)
