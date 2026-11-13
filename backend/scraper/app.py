"""Review API. Run: uv run python -m scraper.app (localhost by default)."""

import sys
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from getpass import getuser
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.cors import CORSMiddleware
from psycopg import Error as PostgresError
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from . import auth, db, master, pipeline
from .db import JobStopped
from .config import RULES_PATH, ROOT, env, env_list, jwt_secret, load_rules

@asynccontextmanager
async def lifespan(app):
    jwt_secret()
    try:
        with db.connect() as conn:
            db.interrupt_stale(conn)
            auth.seed_admin(conn)
            _seed_schedule(conn)
    except (RuntimeError, PostgresError):
        pass  # Keep the GUI available; status reports the error and retries the check.
    stopped = threading.Event()
    if env("DAILY_SCRAPE_AT"):
        logging.getLogger(__name__).warning("DAILY_SCRAPE_AT is ignored; use SCRAPE_EVERY_MINUTES or /api/schedule")
    scheduler = threading.Thread(target=_auto_scrape, args=(stopped,), daemon=True)
    scheduler.start()
    try:
        yield
    finally:
        stopped.set()
        scheduler.join(timeout=1)


def _seed_schedule(conn):
    try:
        interval = int(env("SCRAPE_EVERY_MINUTES"))
    except ValueError:
        interval = 0
    enabled = 5 <= interval <= 1440
    conn.execute("INSERT INTO schedule(id,enabled,interval_minutes) VALUES (1,%s,%s)"
                 " ON CONFLICT DO NOTHING", (enabled, interval if enabled else 15))


def _auto_due(now, enabled, interval_minutes, last_auto_started_at, running):
    return enabled and not running and (
        last_auto_started_at is None or now - last_auto_started_at >= timedelta(minutes=interval_minutes))


def _auto_scrape(stopped):
    logger = logging.getLogger("uvicorn.error")
    while not stopped.is_set():
        try:
            with db.connect() as conn:
                # Serialize checks across workers; start_job still arbitrates against manual jobs.
                locked = conn.execute("SELECT pg_try_advisory_xact_lock(783214901) AS locked").fetchone()["locked"]
                if locked:
                    _seed_schedule(conn)
                    schedule = conn.execute("SELECT enabled,interval_minutes FROM schedule WHERE id=1").fetchone()
                    running = conn.execute("SELECT EXISTS(SELECT 1 FROM jobs WHERE status IN ('running','paused')) AS running").fetchone()["running"]
                    last_auto = conn.execute(
                        "SELECT max(started_at) AS last_auto_at FROM jobs WHERE name='scrape'"
                        " AND params->>'auto'='true'").fetchone()["last_auto_at"]
                    if schedule and not stopped.is_set() and _auto_due(
                            datetime.now(timezone.utc), schedule["enabled"], schedule["interval_minutes"], last_auto, running):
                        params = {"sources": ["gmail", "contacts", "sheets"], "mailboxes": [],
                                  "since": env("SCRAPE_SINCE") or "2018-01-01", "full": False, "auto": True}
                        datetime.strptime(params["since"], "%Y-%m-%d")
                        job_id = start_job("scrape", params, carry=True)
                        logger.info("Auto scrape started: job %s (incremental extraction is included)", job_id)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            logger.info("Auto scrape skipped: %s; retrying in 60 seconds", exc.detail)
        except (PostgresError, RuntimeError, ValueError):
            logger.exception("Auto scrape check failed; retrying in 60 seconds")
        stopped.wait(60)


app = FastAPI(title="Workspace Scraper", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan,
              dependencies=[Depends(auth.current_user)])
app.include_router(auth.router)


@app.exception_handler(RuntimeError)
@app.exception_handler(Exception)
async def config_error(request: Request, exc: Exception):
    logging.getLogger(__name__).error("Unhandled request error", exc_info=(type(exc), exc, exc.__traceback__))
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


@app.middleware("http")
async def require_gui_header(request: Request, call_next):
    # Custom header forces a CORS preflight, so other websites cannot POST here.
    if request.method != "GET" and request.headers.get("x-local-gui") != "1":
        return JSONResponse({"detail": "missing X-Local-GUI header"}, status_code=403)
    return await call_next(request)


def configure_middleware(application):
    # Blocks DNS rebinding: serve only explicitly allowed hosts (localhost by default).
    application.add_middleware(TrustedHostMiddleware,
                               allowed_hosts=env_list("ALLOWED_HOSTS") or ["127.0.0.1", "localhost"])
    origins = [origin for origin in env_list("CORS_ORIGINS") if origin != "*"]
    if origins:
        # Outermost so preflights do not reach the GUI header or authentication checks.
        application.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                                   allow_headers=["Content-Type", "X-Local-GUI"])


configure_middleware(app)


# --- Persistent jobs (one running row across all API processes) ---

HEARTBEAT_SECONDS = 30
PAUSE_POLL_SECONDS = 2


def log(job_id: int, msg: str) -> None:
    try:
        with db.connect() as conn:
            conn.autocommit = True
            conn.execute("INSERT INTO job_log (job_id, line) VALUES (%s,%s)",
                         (job_id, f"{datetime.now():%H:%M:%S} {msg}"))
            conn.execute("UPDATE jobs SET heartbeat_at=clock_timestamp() WHERE id=%s AND status IN ('running','paused')", (job_id,))
    except (PostgresError, RuntimeError) as exc:
        line = msg.replace("\r", "\\r").replace("\n", "\\n")
        print(f"job {job_id} log write failed: {exc!r}; line: {line}", file=sys.stderr)


class JobLog:
    def __init__(self, job_id: int):
        self.job_id = job_id

    def __call__(self, msg: str) -> None:
        log(self.job_id, msg)

    def failure(self, count: int = 1) -> None:
        try:
            with db.connect() as conn:
                conn.autocommit = True
                conn.execute("UPDATE jobs SET failures=failures+%s, heartbeat_at=clock_timestamp()"
                             " WHERE id=%s AND status IN ('running','paused')", (count, self.job_id))
        except (PostgresError, RuntimeError) as exc:
            print(f"job {self.job_id} log write failed: {exc!r}; line: failure({count})", file=sys.stderr)

    def full_reset_done(self) -> None:
        try:
            with db.connect() as conn:
                conn.autocommit = True
                conn.execute("UPDATE jobs SET params=jsonb_set(params, '{full_reset_done}', 'true')"
                             " WHERE id=%s AND status IN ('running','paused')", (self.job_id,))
        except (PostgresError, RuntimeError) as exc:
            print(f"job {self.job_id} log write failed: {exc!r}; line: full_reset_done", file=sys.stderr)


    def _mailbox_write(self, sql, args) -> None:
        try:
            with db.connect() as conn:
                conn.autocommit = True
                conn.execute(sql, args)
        except (PostgresError, RuntimeError) as exc:
            print(f"job {self.job_id} mailbox write failed: {exc!r}", file=sys.stderr)

    def mailboxes(self, mailboxes, done) -> None:
        self._mailbox_write(
            "INSERT INTO job_mailboxes (job_id, mailbox, status)"
            " SELECT %s, mailbox, CASE WHEN mailbox=ANY(%s::text[]) THEN 'skipped' ELSE 'pending' END"
            " FROM unnest(%s::text[]) AS mailbox ON CONFLICT DO NOTHING",
            (self.job_id, list(done), mailboxes))

    def mailbox_status(self, mailbox, status, error=None) -> None:
        if status == "running":
            self._mailbox_write(
                "UPDATE job_mailboxes SET status='running', started_at=coalesce(started_at,clock_timestamp())"
                " WHERE job_id=%s AND mailbox=%s AND status='pending'", (self.job_id, mailbox))
        elif status == "failed":
            self._mailbox_write(
                "UPDATE job_mailboxes SET status='failed', error=coalesce(error,%s),"
                " finished_at=coalesce(finished_at,clock_timestamp())"
                " WHERE job_id=%s AND mailbox=%s AND status NOT IN ('done','skipped')",
                (error, self.job_id, mailbox))
        elif status == "pending":
            self._mailbox_write(
                "UPDATE job_mailboxes SET status='pending' WHERE job_id=%s AND mailbox=%s AND status='running'",
                (self.job_id, mailbox))

    def mailbox_progress(self, mailbox, fetched, total, rate_limited) -> None:
        self._mailbox_write(
            "UPDATE job_mailboxes SET fetched=greatest(fetched,%s), total=greatest(total,%s),"
            " rate_limited=greatest(rate_limited,%s) WHERE job_id=%s AND mailbox=%s",
            (fetched, total, rate_limited, self.job_id, mailbox))

    def mailbox_done(self, mailbox: str) -> None:
        # The resume marker must not depend on the board write: a board failure only costs the display.
        with db.connect() as conn:
            conn.autocommit = True
            conn.execute(
                "UPDATE jobs SET params=jsonb_set(params, '{done_mailboxes}',"
                " coalesce(params->'done_mailboxes','[]') || to_jsonb(%s::text))"
                " WHERE id=%s AND status IN ('running','paused')"
                " AND NOT (coalesce(params->'done_mailboxes','[]') ? %s)", (mailbox, self.job_id, mailbox))
        self._mailbox_write(
            "INSERT INTO job_mailboxes (job_id,mailbox,status,finished_at) VALUES (%s,%s,'done',clock_timestamp())"
            " ON CONFLICT (job_id,mailbox) DO UPDATE SET status='done',finished_at=clock_timestamp()",
            (self.job_id, mailbox))

    def checkpoint(self) -> None:
        while True:
            message = None
            with db.connect() as conn:
                job = conn.execute("SELECT status, control FROM jobs WHERE id=%s FOR UPDATE", (self.job_id,)).fetchone()
                if not job or job["status"] not in ("running", "paused") or job["control"] == "stop":
                    raise JobStopped()
                if job["control"] == "pause" and job["status"] == "running":
                    conn.execute("UPDATE jobs SET status='paused' WHERE id=%s", (self.job_id,))
                    message = "paused"
                elif job["control"] is None and job["status"] == "paused":
                    conn.execute("UPDATE jobs SET status='running' WHERE id=%s", (self.job_id,))
                    message = "continued"
                proceed = job["control"] is None
            if message:
                self(message)
            if proceed:
                return
            time.sleep(PAUSE_POLL_SECONDS)


def _heartbeat(job_id: int, stopped: threading.Event) -> None:
    while not stopped.wait(HEARTBEAT_SECONDS):
        try:
            with db.connect() as conn:
                conn.autocommit = True
                if not conn.execute("UPDATE jobs SET heartbeat_at=clock_timestamp()"
                                    " WHERE id=%s AND status IN ('running','paused') RETURNING id", (job_id,)).fetchone():
                    return
        except PostgresError:
            continue  # A temporary DB outage gets another attempt on the next heartbeat.


def _finish_job(job_id: int, error: str | None, stopped: bool = False) -> str | None:
    with db.connect() as conn:
        row = conn.execute(
            "UPDATE jobs SET status=CASE WHEN %s OR control='stop' THEN 'stopped'"
            " WHEN %s::text IS NOT NULL THEN 'failed'"
            " WHEN failures>0 THEN 'done_with_errors' ELSE 'done' END,"
            " control=NULL, error=%s, finished_at=clock_timestamp(), heartbeat_at=clock_timestamp()"
            " WHERE id=%s AND status IN ('running','paused') RETURNING status", (stopped, error, error, job_id)).fetchone()
    return row["status"] if row else None


def _run_job(job_id: int, name: str, params: dict) -> None:
    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat, args=(job_id, heartbeat_stop), daemon=True)
    heartbeat.start()
    logger, error, stopped = JobLog(job_id), None, False
    try:
        logger.checkpoint()
        if name == "scrape":
            pipeline.run_scrape(**{key: params[key] for key in ("sources", "mailboxes", "since", "full")},
                                log=logger, checkpoint=logger.checkpoint,
                                done_mailboxes=params.get("done_mailboxes", []))
        elif name == "extract":
            pipeline.run_extract(logger, full=params.get("full", False) and not params.get("full_reset_done", False),
                                 checkpoint=logger.checkpoint)
        else:
            raise RuntimeError(f"Unknown job: {name}")
        logger.checkpoint()
    except JobStopped:
        stopped = True
    except Exception as exc:
        error = repr(exc)
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=1)
        result = _finish_job(job_id, error, stopped)
        # Finalize before logging, so a late Stop cannot produce a misleading 'finished'.
        if result == "stopped":
            logger("stopped")
        elif result == "failed":
            logger(f"ERROR: {error}")
        elif result in ("done", "done_with_errors"):
            logger(f"{name}: finished")


def _launch_job(job_id: int, name: str, params: dict) -> None:
    threading.Thread(target=_run_job, args=(job_id, name, params), daemon=True).start()


def start_job(name: str, params: dict, *, resume_id: int | None = None, carry: bool = False) -> int:
    try:
        with db.connect() as conn:
            carried_ids = []
            if resume_id is not None:
                previous = conn.execute("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (resume_id,)).fetchone()
                if not previous or previous["status"] not in ("interrupted", "stopped", "failed"):
                    raise HTTPException(400, "job is not interrupted, stopped or failed")
                name, params = previous["name"], dict(previous["params"])
                if name == "extract" and params.get("full_reset_done"):
                    params["full"] = False
            if carry or (resume_id is not None and name == "scrape"):
                previous_jobs = conn.execute(
                    "SELECT id,params FROM jobs WHERE name='scrape'"
                    " AND status IN ('interrupted','stopped','failed')"
                    " AND (%s::bigint IS NULL OR id<%s)"
                    " AND id > COALESCE((SELECT max(id) FROM jobs WHERE name='scrape'"
                    " AND status IN ('done','done_with_errors')),0) ORDER BY id DESC FOR UPDATE",
                    (resume_id, resume_id),
                ).fetchall()
                done_mailboxes = set(params.get("done_mailboxes", []))
                match_keys = ("since", "sources", "mailboxes", "full") if resume_id is not None else ("since", "sources", "mailboxes")
                for previous in previous_jobs:
                    if all(previous["params"].get(key) == params.get(key) for key in match_keys):
                        carried_ids.append(previous["id"])
                        done_mailboxes.update(previous["params"].get("done_mailboxes", []))
                if carried_ids:
                    params["done_mailboxes"] = sorted(done_mailboxes)
            job_id = conn.execute("INSERT INTO jobs (name, params, status) VALUES (%s,%s,'running') RETURNING id",
                                  (name, Jsonb(params))).fetchone()["id"]
            if resume_id is not None:
                conn.execute("UPDATE jobs SET status='done', control=NULL, error=%s WHERE id=%s AND status IN ('interrupted','stopped','failed')",
                             (f"resumed as job {job_id}", resume_id))
            if carried_ids:
                conn.execute("UPDATE jobs SET status='done', control=NULL, error=%s WHERE id=ANY(%s)",
                             (f"continued as job {job_id}", carried_ids))
    except UniqueViolation as exc:
        raise HTTPException(409, "another job is still running") from exc
    try:
        _launch_job(job_id, name, params)
    except Exception as exc:
        _finish_job(job_id, repr(exc))
        raise
    return job_id


@app.post("/api/jobs/{job_id}/resume", dependencies=[Depends(auth.require_route("scraping"))])
def resume(job_id: int):
    successor = start_job("", {}, resume_id=job_id)
    return {"started": True, "job_id": successor}


def _control(job_id: int, action: str) -> dict:
    conditions = {
        "pause": ("status='running' AND control IS NULL", "pause"),
        "continue": ("control IS DISTINCT FROM 'stop' AND (status='paused' OR (status='running' AND control='pause'))", None),
        "stop": ("status IN ('running','paused') AND control IS DISTINCT FROM 'stop'", "stop"),
    }
    condition, value = conditions[action]
    with db.connect() as conn:
        row = conn.execute("UPDATE jobs SET control=%s WHERE id=%s AND (" + condition + ") RETURNING id",
                           (value, job_id)).fetchone()
        if not row:
            raise HTTPException(400, f"cannot {action} this job in its current state")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/pause", dependencies=[Depends(auth.require_route("scraping"))])
def pause(job_id: int):
    return _control(job_id, "pause")


@app.post("/api/jobs/{job_id}/continue", dependencies=[Depends(auth.require_route("scraping"))])
def continue_job(job_id: int):
    return _control(job_id, "continue")


@app.post("/api/jobs/{job_id}/stop", dependencies=[Depends(auth.require_route("scraping"))])
def stop(job_id: int):
    return _control(job_id, "stop")


class ScheduleIn(BaseModel):
    enabled: bool
    interval_minutes: int = Field(ge=5, le=1440)


def _schedule(conn):
    row = conn.execute(
        "SELECT enabled,interval_minutes,"
        " (SELECT max(started_at) FROM jobs WHERE name='scrape' AND params->>'auto'='true') AS last_auto_at,"
        " EXISTS(SELECT 1 FROM jobs WHERE status IN ('running','paused')) AS running"
        " FROM schedule WHERE id=1").fetchone()
    running = row.pop("running")
    now = datetime.now(timezone.utc)
    next_run = row["last_auto_at"] + timedelta(minutes=row["interval_minutes"]) if row["last_auto_at"] else now
    row["next_run_at"] = next_run if row["enabled"] and not (running and next_run <= now) else None
    return row


@app.get("/api/schedule", dependencies=[Depends(auth.require_route("scraping"))])
def get_schedule():
    with db.connect() as conn:
        _seed_schedule(conn)
        return _schedule(conn)


@app.put("/api/schedule", dependencies=[Depends(auth.require_route("scraping"))])
def put_schedule(body: ScheduleIn):
    with db.connect() as conn:
        _seed_schedule(conn)
        conn.execute("UPDATE schedule SET enabled=%s,interval_minutes=%s,updated_at=clock_timestamp() WHERE id=1",
                     (body.enabled, body.interval_minutes))
        return _schedule(conn)


class ScrapeIn(BaseModel):
    sources: list[Literal["gmail", "contacts", "sheets"]]
    mailboxes: list[str] = []
    since: str = ""
    full: bool = False
    fresh: bool = False


@app.post("/api/scrape", dependencies=[Depends(auth.require_route("scraping"))])
def scrape(body: ScrapeIn):
    since = body.since or env("SCRAPE_SINCE", "2018-01-01")
    datetime.strptime(since, "%Y-%m-%d")  # validate before starting
    mailboxes = [m.strip().lower() for m in body.mailboxes if m.strip()]
    params = {"sources": body.sources, "mailboxes": mailboxes, "since": since, "full": body.full}
    start_job("scrape", params, carry=not body.full and not body.fresh)
    return {"started": True, "carried": len(params.get("done_mailboxes", []))}


class ExtractIn(BaseModel):
    full: bool = False


@app.post("/api/extract", dependencies=[Depends(auth.require_route("scraping"))])
def extract(body: ExtractIn):
    start_job("extract", {"full": body.full})
    return {"started": True}


@app.get("/api/status")
def status(user=Depends(auth.current_user)):
    scraping = "scraping" in user["routes"]
    with db.connect() as conn:
        db.interrupt_stale(conn)
        job = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
        lines = conn.execute("SELECT line FROM job_log WHERE job_id=%s ORDER BY id DESC LIMIT 500",
                             (job["id"],)).fetchall() if job and scraping else []
        counts = {r["review_state"]: r["n"] for r in conn.execute(
            "SELECT review_state, count(*) AS n FROM candidates GROUP BY review_state")}
        raw = {r["source"]: r["n"] for r in conn.execute(
            "SELECT source, count(*) AS n FROM raw_items GROUP BY source")}
        ready = conn.execute(
            "SELECT count(*) AS n FROM candidates WHERE review_state='approved'"
            " AND (published_at IS NULL OR reviewed_at>published_at)").fetchone()["n"]
        failed = conn.execute("SELECT count(*) AS n FROM raw_items WHERE extract_error IS NOT NULL").fetchone()["n"]
    result = {"running": bool(job and job["status"] in ("running", "paused")), "job": job["name"] if job else "",
            "error": job["error"] if job else None, "log": [r["line"] for r in reversed(lines)],
            "candidates": counts, "raw_items": raw, "ready_to_publish": ready, "extract_failed": failed,
            "job_control": job["control"] if job else None,
            "job_id": job["id"] if job else None, "job_status": job["status"] if job else None,
            "job_params": job["params"] if job else {}, "job_failures": job["failures"] if job else 0,
            "job_started_at": job["started_at"] if job else None, "job_finished_at": job["finished_at"] if job else None}
    if not scraping:
        for key in ("log", "job_params", "error"):
            del result[key]
    return result


# --- Review ---

def _mailboxes(conn, job_id):
    items = conn.execute(
        "SELECT m.mailbox,m.status,m.fetched,m.total,m.rate_limited,m.error,m.started_at,m.finished_at,s.last_synced"
        " FROM job_mailboxes m LEFT JOIN (SELECT mailbox,max(updated_at) AS last_synced"
        " FROM sync_state WHERE source='gmail' GROUP BY mailbox) s ON s.mailbox=m.mailbox"
        " WHERE m.job_id=%s ORDER BY CASE m.status WHEN 'running' THEN 0"
        " WHEN 'failed' THEN 1 WHEN 'pending' THEN 2 WHEN 'done' THEN 3 ELSE 4 END, mailbox",
        (job_id,)).fetchall()
    counts = dict.fromkeys(("pending", "running", "done", "failed", "skipped"), 0)
    for item in items:
        counts[item["status"]] += 1
    synced_before = sum(item["last_synced"] is not None or item["status"] in ("done", "skipped") for item in items)
    return {"job_id": job_id, "items": items, "counts": counts, "synced_before": synced_before}


@app.get("/api/jobs/latest/mailboxes", dependencies=[Depends(auth.require_route("scraping"))])
def latest_mailboxes():
    with db.connect() as conn:
        job = conn.execute("SELECT id FROM jobs WHERE name='scrape' ORDER BY id DESC LIMIT 1").fetchone()
        job_id = job["id"] if job else None
        return _mailboxes(conn, job_id)


@app.get("/api/jobs/{job_id}/mailboxes", dependencies=[Depends(auth.require_route("scraping"))])
def job_mailboxes(job_id: int):
    with db.connect() as conn:
        return _mailboxes(conn, job_id)


@app.get("/api/jobs/{job_id}/log", dependencies=[Depends(auth.require_route("scraping"))])
def job_log(job_id: int, mailbox: str = "", limit: int = Query(default=1000, ge=1, le=5000)):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT line FROM job_log WHERE job_id=%s AND strpos(line,%s)>0 ORDER BY id DESC LIMIT %s",
            (job_id, mailbox, limit)).fetchall()
        return {"lines": [row["line"] for row in reversed(rows)]}


@app.get("/api/master/summary", dependencies=[Depends(auth.require_route("review"))])
def master_summary():
    with db.connect() as conn:
        return master.summary(conn)


def _candidate_where(state: str = "pending", q: str = "", status: str = "", department: str = "", stage: str = "",
                     mailbox: str = "", service: str = "", domain: str = "",
                     has_phone: Literal["", "true", "false"] = "", has_amount: Literal["", "true", "false"] = "",
                     date_from: str = "", date_to: str = "", match: Literal["", "email", "domain", "none", "any"] = ""):
    sql = " FROM candidates WHERE review_state=%s"
    args = [state]
    if q:
        sql += " AND (email_key ILIKE %s OR fields->'name'->>'value' ILIKE %s OR fields->'company'->>'value' ILIKE %s)"
        args += [f"%{q}%"] * 3
    if status:
        sql += " AND fields->'status'->>'value' = %s"
        args.append(status)
    if stage:
        sql += " AND coalesce(fields->'stage'->>'value', 'inquiry') = %s"
        args.append(stage)
    if department:
        sql += " AND fields->'department'->>'value' = %s"
        args.append(department)
    if mailbox:
        sql += " AND jsonb_path_exists(fields, '$.handlers.value[*] ? (@.mailbox == $mailbox)', jsonb_build_object('mailbox', %s::text))"
        args.append(mailbox)
    if service:
        sql += " AND (fields->'services_availed'->'value' ? %s OR fields->'services'->'value' ? %s)"
        args.extend([service, service])
    if domain:
        sql += " AND lower(split_part(email_key,'@',2)) = %s"
        args.append(domain.lstrip('@').lower())
    for key, enabled in (("phones", has_phone), ("amounts", has_amount)):
        if enabled == "true":
            sql += f" AND jsonb_typeof(fields->'{key}'->'value')='array' AND fields->'{key}'->'value' <> '[]'::jsonb"
    for key, value, operator in (("date_from", date_from, ">="), ("date_to", date_to, "<=")):
        if value:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise HTTPException(400, f"{key} must be YYYY-MM-DD") from None
            sql += f" AND left(fields->'last_contact'->>'value',10) {operator} %s"
            args.append(value)
    if match == "none":
        sql += " AND (review_state<>'pending' OR master_match IS NULL)"
    elif match:
        sql += " AND review_state='pending' AND master_match" + (" IS NOT NULL" if match == "any" else "=%s")
        if match != "any":
            args.append(match)
    return sql, args


@app.get("/api/candidates", dependencies=[Depends(auth.require_route("review"))])
def list_candidates(response: Response, sort: str = "last_contact", order: str = "desc",
                    page: int = Query(default=1, ge=1), size: int = Query(default=500, ge=1, le=500),
                    selected=Depends(_candidate_where)):
    if sort not in {"last_contact", "first_contact"} or order not in {"asc", "desc"}:
        raise HTTPException(400, "sort must be last_contact or first_contact; order must be asc or desc")
    order_by = f" ORDER BY fields->'{sort}'->>'value' {order} NULLS LAST, id ASC"
    sql, args = selected
    with db.connect() as conn:
        master_names = ("ARRAY(SELECT m.name FROM companies m WHERE c.review_state='pending'"
                        " AND m.id=ANY(c.master_ids) ORDER BY m.id)" if master.available(conn) else "ARRAY[]::text[]")
        response.headers["X-Total-Count"] = str(conn.execute("SELECT count(*) AS n" + sql, args).fetchone()["n"])
        return conn.execute(
            "WITH RECURSIVE selected AS (SELECT *" + sql +
            order_by + " LIMIT %s OFFSET %s),"
            " lineage AS (SELECT id AS root_id,id,email_key,ARRAY[id] AS path FROM selected"
            " UNION ALL SELECT l.root_id,c.id,c.email_key,l.path || c.id FROM lineage l"
            " JOIN candidates c ON c.merged_into=l.id AND c.review_state='merged' WHERE NOT c.id=ANY(l.path)),"
            " item_counts AS (SELECT root_id,count(DISTINCT s.raw_item_id) AS items,"
            " count(DISTINCT s.raw_item_id) FILTER (WHERE r.source='gmail') AS gmail,"
            " count(DISTINCT s.raw_item_id) FILTER (WHERE r.source='contacts') AS contacts,"
            " count(DISTINCT s.raw_item_id) FILTER (WHERE r.source='sheets') AS sheets FROM lineage l"
            " JOIN signals s ON s.key=l.email_key JOIN raw_items r ON r.id=s.raw_item_id GROUP BY root_id)"
            " SELECT c.id,c.email_key,c.review_state,fields->'name'->>'value' AS name,"
            " CASE WHEN c.review_state='pending' THEN c.master_match END AS master_match,"
            + master_names + " AS master_names,"
            " fields->'status'->>'value' AS status,fields->'department'->>'value' AS department,"
            " coalesce((fields->'engagement_paid'->>'value')::boolean,false) AS paid,"
            " coalesce(fields->'stage'->>'value','inquiry') AS stage,"
            " coalesce(jsonb_array_length(fields->'needs_check'->'value'),0) AS needs_check,"
            " fields->'last_contact'->>'value' AS last_contact,fields->'first_contact'->>'value' AS first_contact,"
            " fields->'company'->>'value' AS company,"
            " coalesce(nullif(fields->'phones'->'value','null'::jsonb),'[]'::jsonb) AS phones,"
            " coalesce(nullif(nullif(fields->'services_availed'->'value','null'::jsonb),'[]'::jsonb),"
            " nullif(fields->'services'->'value','null'::jsonb),'[]'::jsonb) AS services,"
            " (SELECT jsonb_build_object('amount',a->'amount','currency',a->'currency',"
            " 'stage',a->'stage','date',a->'date') FROM jsonb_array_elements("
            " coalesce(nullif(fields->'amounts'->'value','null'::jsonb),'[]'::jsonb)) AS a"
            " ORDER BY a->>'date' DESC NULLS LAST LIMIT 1) AS latest_amount,"
            " jsonb_build_object('gmail',coalesce(i.gmail,0),'contacts',coalesce(i.contacts,0),"
            " 'sheets',coalesce(i.sheets,0)) AS item_sources,"
            " coalesce(i.items,0) AS items FROM selected c LEFT JOIN item_counts i ON i.root_id=c.id"
            + order_by, [*args, size, (page - 1) * size]).fetchall()


@app.get("/api/candidates/timeline", dependencies=[Depends(auth.require_route("review"))])
def candidate_timeline(bucket: Literal["day", "week"] = "day",
                       field: Literal["last_contact", "first_contact"] = "last_contact", selected=Depends(_candidate_where)):
    sql, args = selected
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT left(fields->%s->>'value',10) AS day,count(*) AS total,"
            " count(*) FILTER (WHERE review_state='pending' AND master_match='email') AS email,"
            " count(*) FILTER (WHERE review_state='pending' AND master_match='domain') AS domain"
            + sql + " GROUP BY 1", [field, *args]).fetchall()
    groups = {}
    for row in rows:
        try:
            day = date.fromisoformat(row["day"])
            if day.isoformat() != row["day"]:
                raise ValueError()
            if bucket == "week":
                day -= timedelta(days=day.weekday())
            key = day.isoformat()
        except (TypeError, ValueError):
            key = None
        group = groups.setdefault(key, {"bucket": key, "total": 0, "email": 0, "domain": 0})
        for metric in ("total", "email", "domain"):
            group[metric] += row[metric]
    return [groups[key] for key in sorted(groups, key=lambda key: (key is not None, key or ""), reverse=True)]


@app.get("/api/candidates/facets", dependencies=[Depends(auth.require_route("review"))])
def candidate_facets(state: str = "pending"):
    with db.connect() as conn:
        rows = conn.execute(
            "WITH selected AS (SELECT fields FROM candidates WHERE review_state=%s),"
            " facets AS (SELECT 'departments' AS kind, fields->'department'->>'value' AS value FROM selected"
            " UNION ALL SELECT 'mailboxes', value #>> '{}' FROM selected,"
            " LATERAL jsonb_path_query(fields, '$.handlers.value[*].mailbox') AS value"
            " UNION ALL SELECT 'services', value #>> '{}' FROM selected,"
            " LATERAL jsonb_path_query(fields, '$.services_availed.value[*]') AS value"
            " UNION ALL SELECT 'services', value #>> '{}' FROM selected,"
            " LATERAL jsonb_path_query(fields, '$.services.value[*]') AS value)"
            " SELECT DISTINCT kind,value FROM facets WHERE btrim(value) <> '' ORDER BY kind,value", (state,)).fetchall()
    return {kind: [row['value'] for row in rows if row['kind'] == kind]
            for kind in ("departments", "mailboxes", "services")}


def _get(conn, cid: int, lock: bool = False) -> dict:
    row = conn.execute("SELECT * FROM candidates WHERE id = %s" + (" FOR UPDATE" if lock else ""), (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "candidate not found")
    if row["review_state"] != "pending":
        row["master_match"] = row["master_ids"] = None
    return row


@app.get("/api/candidates/{cid}", dependencies=[Depends(auth.require_route("review"))])
def get_candidate(cid: int):
    with db.connect() as conn:
        return _get(conn, cid)


class AmountIn(BaseModel):
    stage: Literal["quoted", "invoiced"]
    amount: float = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(min_length=3, max_length=3)
    date: datetime | None = None
    raw_item_id: int


class PaymentIn(BaseModel):
    date: datetime | None = None
    amount: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    basis: Literal["stated", "invoice", "unknown", "manual"]
    raw_item_id: int | None = None

    @field_validator("date")
    @classmethod
    def dated_payment(cls, value):
        # Date-only editor values have no offset; store them at midnight UTC.
        return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value

    @model_validator(mode="after")
    def amount_and_basis(self):
        if (self.amount is None) != (self.currency is None):
            raise ValueError("payment amount and currency must both be set or both be null")
        if (self.basis == "unknown") != (self.amount is None):
            raise ValueError("payment basis must be unknown exactly when the amount is null")
        return self


class HandlerIn(BaseModel):
    department: str
    mailbox: str
    first: datetime | None = None
    last: datetime | None = None
    messages: int = Field(ge=0)


class ClientIn(BaseModel):
    name: str | None = None
    emails: list[str] = []
    phones: list[str] = []
    company: str | None = None
    status: Literal["inquiry", "active", "past", "lost"]
    services: list[str] = []
    department: str | None = None
    engagement_paid: bool = False
    stage: Literal["inquiry", "quoted", "invoiced", "paid"] = "inquiry"
    amounts: list[AmountIn] = []
    payments: list[PaymentIn] = []
    services_availed: list[str] = []
    handlers: list[HandlerIn] = []
    first_contact: datetime | None = None
    last_contact: datetime | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def paid_details(self):
        if self.payments and not self.engagement_paid:
            raise ValueError("payment rows require engagement_paid")
        return self


@app.post("/api/candidates/{cid}/approve", dependencies=[Depends(auth.require_route("review"))])
def approve(cid: int, c: ClientIn):
    with db.connect() as conn:
        cand = _get(conn, cid, lock=True)
        _approve(conn, cand, c)
    return {"ok": True}


def _approve(conn, cand: dict, c: ClientIn):
    if cand["review_state"] == "merged":
        raise HTTPException(409, "reopen the merged record before approving it")
    values = c.model_dump(mode="json")
    for name in ("payments", "services_availed", "handlers"):
        if name not in c.model_fields_set:
            values[name] = (cand["fields"].get(name) or {}).get("value", [])
    try:
        reviewed = ClientIn.model_validate(values)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors(include_context=False)) from exc
    values = reviewed.model_dump(mode="json")
    since = min((p.date for p in reviewed.payments if p.date), default=None)
    values["client_since"] = since.isoformat() if since else None
    conn.execute(
        "UPDATE candidates SET review_state='approved', master_match=NULL,master_ids=NULL, reviewed=%s, reviewed_by=%s,"
        " reviewed_at=clock_timestamp(), updated_at=now() WHERE id=%s",
        (Jsonb(values), getuser(), cand["id"]))


@app.post("/api/publish", dependencies=[Depends(auth.require_route("review"))])
def publish():
    inserted = updated = 0
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM candidates WHERE review_state='approved'"
            " AND (published_at IS NULL OR reviewed_at>published_at) ORDER BY id FOR UPDATE").fetchall()
        for cand in rows:
            # Recheck the row returned after acquiring the same lock used by approve.
            if cand["review_state"] != "approved" or (cand["published_at"] is not None and (
                    cand["reviewed_at"] is None or cand["reviewed_at"] <= cand["published_at"])):
                continue
            c = ClientIn.model_validate(cand["reviewed"])
            since = min((p.date for p in c.payments if p.date), default=None)
            vals = (c.name, c.emails, c.phones, c.company, c.status, c.services, c.department,
                    c.engagement_paid, c.first_contact, c.last_contact, c.notes, cand["reviewed_by"],
                    cand["reviewed_at"], c.stage, Jsonb([a.model_dump(mode="json") for a in c.amounts]),
                    Jsonb([p.model_dump(mode="json") for p in c.payments]), since, c.services_availed,
                    Jsonb([h.model_dump(mode="json") for h in c.handlers]))
            if cand["client_id"] is not None:
                conn.execute(
                    "UPDATE clients SET name=%s, emails=%s, phones=%s, company=%s, status=%s, services=%s,"
                    " department=%s, engagement_paid=%s, first_contact=%s, last_contact=%s, notes=%s,"
                    " approved_by=%s, approved_at=%s, stage=%s, amounts=%s, payments=%s, client_since=%s,"
                    " services_availed=%s, departments=%s, updated_at=now() WHERE id=%s",
                    (*vals, cand["client_id"]))
                client_id = cand["client_id"]
                updated += 1
            else:
                client_id = conn.execute(
                    "INSERT INTO clients (name, emails, phones, company, status, services, department,"
                    " engagement_paid, first_contact, last_contact, notes, approved_by, approved_at, stage, amounts,"
                    " payments, client_since, services_availed, departments)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", vals).fetchone()["id"]
                inserted += 1
            conn.execute("UPDATE candidates SET client_id=%s, published_at=clock_timestamp(), updated_at=now() WHERE id=%s",
                         (client_id, cand["id"]))
    return {"published": inserted + updated, "inserted": inserted, "updated": updated}


@app.post("/api/candidates/{cid}/reject", dependencies=[Depends(auth.require_route("review"))])
def reject(cid: int):
    with db.connect() as conn:
        _get(conn, cid)
        _reject(conn, cid)
    return {"ok": True}


def _reject(conn, cid: int):
    conn.execute("UPDATE candidates SET review_state='rejected', master_match=NULL,master_ids=NULL, updated_at=now() WHERE id=%s", (cid,))


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["approve", "reject"]

    @field_validator("ids")
    @classmethod
    def unique_ids(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("ids must be unique")
        return value


def _extracted_client(fields: dict) -> ClientIn:
    values = {key: (fields.get(key) or {}).get("value") for key in ClientIn.model_fields}
    for keys, item_type in ((("emails", "phones", "services", "services_availed"), str),
                            (("amounts", "handlers", "payments"), dict)):
        for key in keys:
            value = values[key]
            if value is not None and (not isinstance(value, list) or
                                      any(not isinstance(item, item_type) for item in value)):
                raise HTTPException(422, "invalid extracted field shapes")
            values[key] = value if value is not None else []
    for key in ("name", "company", "department"):
        values[key] = (values[key] or "").strip() or None
    for key in ("emails", "phones", "services", "services_availed"):
        values[key] = [part.strip() for part in ", ".join(values[key]).split(",") if part.strip()]
    values["payments"] = [dict(
        date=p.get("date") or None, amount=p.get("amount"),
        currency=(p.get("currency") or "").strip().upper() or None,
        basis="unknown" if p.get("amount") is None else
              "manual" if p.get("basis", "manual") == "unknown" else p.get("basis", "manual"),
        raw_item_id=p.get("raw_item_id")) for p in values["payments"]]
    values.update(stage=values["stage"] or "inquiry", status=values["status"] or "inquiry",
                  engagement_paid=bool(values["engagement_paid"]), notes=None)
    return ClientIn.model_validate(values)


@app.post("/api/candidates/bulk", dependencies=[Depends(auth.require_route("review"))])
def bulk_candidates(body: BulkIn):
    done, failed = [], []
    for cid in body.ids:
        try:
            with db.connect() as conn:
                cand = _get(conn, cid, lock=True)
                if cand["review_state"] != "pending":
                    raise HTTPException(409, "only pending records can be bulk reviewed")
                if body.action == "approve":
                    try:
                        client = _extracted_client(cand["fields"])
                    except (TypeError, AttributeError) as exc:
                        raise HTTPException(422, "invalid extracted field shapes") from exc
                    _approve(conn, cand, client)
                else:
                    _reject(conn, cid)
            done.append(cid)
        except (HTTPException, ValidationError, PostgresError) as exc:
            error = str(exc.detail) if isinstance(exc, HTTPException) else (
                str(exc) if isinstance(exc, ValidationError) else "database operation failed")
            failed.append({"id": cid, "error": error})
    return {"done": done, "failed": failed}


def _previous_target(conn, cand: dict) -> set[str]:
    return {_get(conn, cand["merged_into"])["email_key"]} if cand["merged_into"] else set()


@app.post("/api/candidates/{cid}/reopen", dependencies=[Depends(auth.require_route("review"))])
def reopen(cid: int):
    with db.connect() as conn:
        conn.execute(master.LOCK)
        cand = _get(conn, cid, lock=True)
        keys = {cand["email_key"]} | _previous_target(conn, cand)
        conn.execute("UPDATE candidates SET review_state='pending', merged_into=NULL, updated_at=now() WHERE id=%s", (cid,))
        pipeline.rebuild(conn, keys, load_rules(), datetime.now(timezone.utc))
    return {"ok": True}


class MergeIn(BaseModel):
    into_id: int


@app.post("/api/candidates/{cid}/merge", dependencies=[Depends(auth.require_route("review"))])
def merge(cid: int, m: MergeIn):
    if cid == m.into_id:
        raise HTTPException(400, "cannot merge a record into itself")
    with db.connect() as conn:
        conn.execute(master.LOCK)
        # Lock the source and target in id order.
        locked = {i: _get(conn, i, lock=True) for i in sorted((cid, m.into_id))}
        src, dst = locked[cid], locked[m.into_id]
        if dst["review_state"] == "merged":
            raise HTTPException(400, "target is itself merged; pick the record it was merged into")
        keys = {src["email_key"], dst["email_key"]} | _previous_target(conn, src)
        conn.execute("UPDATE candidates SET review_state='merged', master_match=NULL,master_ids=NULL, merged_into=%s, updated_at=now() WHERE id=%s", (m.into_id, cid))
        pipeline.rebuild(conn, keys, load_rules(), datetime.now(timezone.utc))
    return {"ok": True}


@app.get("/api/raw/{item_id}", dependencies=[Depends(auth.require_route("review"))])
def raw_item(item_id: int):
    """Full source item, for checking evidence."""
    with db.connect() as conn:
        row = conn.execute("SELECT id, source, mailbox, source_id, payload FROM raw_items WHERE id=%s",
                           (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "raw item not found")
    return row


@app.get("/api/rules", dependencies=[Depends(auth.require_route("review"))])
def rules():
    return {"path": str(RULES_PATH), "text": RULES_PATH.read_text()}


@app.get("/")
def index():
    path = ROOT / "scraper" / "static" / "index.html"
    return FileResponse(path) if path.is_file() else PlainTextResponse("Open the review GUI at http://127.0.0.1:3000")


if __name__ == "__main__":
    uvicorn.run(app, host=env("HOST", "127.0.0.1"),
                port=int(env("PORT") or env("GUI_PORT", "8765")), proxy_headers=False)
