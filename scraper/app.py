"""Local review GUI. Run: uv run python -m scraper.app  (binds 127.0.0.1 only)."""

import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from getpass import getuser
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from psycopg import Error as PostgresError
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from . import db, pipeline
from .config import RULES_PATH, ROOT, env, load_rules

@asynccontextmanager
async def lifespan(app):
    try:
        with db.connect() as conn:
            db.interrupt_stale(conn)
    except (RuntimeError, PostgresError):
        pass  # Keep the GUI available; status reports the error and retries the check.
    yield


app = FastAPI(title="Workspace Scraper", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
# Blocks DNS-rebinding: only requests addressed to localhost are served.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.exception_handler(RuntimeError)
async def config_error(request: Request, exc: RuntimeError):
    # Our RuntimeErrors are setup problems (missing .env values); show them in the GUI.
    return JSONResponse({"detail": str(exc)}, status_code=500)


@app.middleware("http")
async def require_gui_header(request: Request, call_next):
    # Custom header forces a CORS preflight, so other websites cannot POST here.
    if request.method != "GET" and request.headers.get("x-local-gui") != "1":
        return JSONResponse({"detail": "missing X-Local-GUI header"}, status_code=403)
    return await call_next(request)


# --- Persistent jobs (one running row across all API processes) ---

HEARTBEAT_SECONDS = 30


def log(job_id: int, msg: str) -> None:
    with db.connect() as conn:
        conn.autocommit = True
        conn.execute("INSERT INTO job_log (job_id, line) VALUES (%s,%s)",
                     (job_id, f"{datetime.now():%H:%M:%S} {msg}"))
        conn.execute("UPDATE jobs SET heartbeat_at=clock_timestamp() WHERE id=%s AND status='running'", (job_id,))


class JobLog:
    def __init__(self, job_id: int):
        self.job_id = job_id

    def __call__(self, msg: str) -> None:
        log(self.job_id, msg)

    def failure(self, count: int = 1) -> None:
        with db.connect() as conn:
            conn.autocommit = True
            conn.execute("UPDATE jobs SET failures=failures+%s, heartbeat_at=clock_timestamp()"
                         " WHERE id=%s AND status='running'", (count, self.job_id))

    def full_reset_done(self) -> None:
        with db.connect() as conn:
            conn.autocommit = True
            conn.execute("UPDATE jobs SET params=jsonb_set(params, '{full_reset_done}', 'true')"
                         " WHERE id=%s AND status='running'", (self.job_id,))


def _heartbeat(job_id: int, stopped: threading.Event) -> None:
    while not stopped.wait(HEARTBEAT_SECONDS):
        try:
            with db.connect() as conn:
                conn.autocommit = True
                if not conn.execute("UPDATE jobs SET heartbeat_at=clock_timestamp()"
                                    " WHERE id=%s AND status='running' RETURNING id", (job_id,)).fetchone():
                    return
        except PostgresError:
            continue  # A temporary DB outage gets another attempt on the next heartbeat.


def _finish_job(job_id: int, error: str | None) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=CASE WHEN %s::text IS NOT NULL THEN 'failed'"
            " WHEN failures>0 THEN 'done_with_errors' ELSE 'done' END,"
            " error=%s, finished_at=clock_timestamp(), heartbeat_at=clock_timestamp()"
            " WHERE id=%s AND status='running'", (error, error, job_id))


def _run_job(job_id: int, name: str, params: dict) -> None:
    stopped = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat, args=(job_id, stopped), daemon=True)
    heartbeat.start()
    logger, error = JobLog(job_id), None
    try:
        if name == "scrape":
            pipeline.run_scrape(**params, log=logger)
        elif name == "extract":
            pipeline.run_extract(logger, full=params.get("full", False))
        else:
            raise RuntimeError(f"Unknown job: {name}")
        logger(f"{name}: finished")
    except Exception as exc:
        error = repr(exc)
        logger(f"ERROR: {error}")
    finally:
        stopped.set()
        heartbeat.join(timeout=1)
        _finish_job(job_id, error)


def _launch_job(job_id: int, name: str, params: dict) -> None:
    threading.Thread(target=_run_job, args=(job_id, name, params), daemon=True).start()


def start_job(name: str, params: dict, *, resume_id: int | None = None) -> int:
    try:
        with db.connect() as conn:
            if resume_id is not None:
                previous = conn.execute("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (resume_id,)).fetchone()
                if not previous or previous["status"] != "interrupted":
                    raise HTTPException(400, "job is not interrupted")
                name, params = previous["name"], dict(previous["params"])
                if name == "extract" and params.get("full_reset_done"):
                    params["full"] = False
            job_id = conn.execute("INSERT INTO jobs (name, params, status) VALUES (%s,%s,'running') RETURNING id",
                                  (name, Jsonb(params))).fetchone()["id"]
            if resume_id is not None:
                conn.execute("UPDATE jobs SET status='done', error=%s WHERE id=%s AND status='interrupted'",
                             (f"resumed as job {job_id}", resume_id))
    except UniqueViolation as exc:
        raise HTTPException(409, "another job is still running") from exc
    try:
        _launch_job(job_id, name, params)
    except Exception as exc:
        _finish_job(job_id, repr(exc))
        raise
    return job_id


@app.post("/api/jobs/{job_id}/resume")
def resume(job_id: int):
    successor = start_job("", {}, resume_id=job_id)
    return {"started": True, "job_id": successor}


class ScrapeIn(BaseModel):
    sources: list[Literal["gmail", "contacts", "sheets"]]
    mailboxes: list[str] = []
    since: str = ""
    full: bool = False


@app.post("/api/scrape")
def scrape(body: ScrapeIn):
    since = body.since or env("SCRAPE_SINCE", "2018-01-01")
    datetime.strptime(since, "%Y-%m-%d")  # validate before starting
    mailboxes = [m.strip().lower() for m in body.mailboxes if m.strip()]
    start_job("scrape", {"sources": body.sources, "mailboxes": mailboxes, "since": since, "full": body.full})
    return {"started": True}


class ExtractIn(BaseModel):
    full: bool = False


@app.post("/api/extract")
def extract(body: ExtractIn):
    start_job("extract", {"full": body.full})
    return {"started": True}


@app.get("/api/status")
def status():
    with db.connect() as conn:
        db.interrupt_stale(conn)
        job = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
        lines = conn.execute("SELECT line FROM job_log WHERE job_id=%s ORDER BY id DESC LIMIT 500",
                             (job["id"],)).fetchall() if job else []
        counts = {r["review_state"]: r["n"] for r in conn.execute(
            "SELECT review_state, count(*) AS n FROM candidates GROUP BY review_state")}
        raw = {r["source"]: r["n"] for r in conn.execute(
            "SELECT source, count(*) AS n FROM raw_items GROUP BY source")}
        ready = conn.execute(
            "SELECT count(*) AS n FROM candidates WHERE review_state='approved'"
            " AND (published_at IS NULL OR reviewed_at>published_at)").fetchone()["n"]
        failed = conn.execute("SELECT count(*) AS n FROM raw_items WHERE extract_error IS NOT NULL").fetchone()["n"]
    return {"running": bool(job and job["status"] == "running"), "job": job["name"] if job else "",
            "error": job["error"] if job else None, "log": [r["line"] for r in reversed(lines)],
            "candidates": counts, "raw_items": raw, "ready_to_publish": ready, "extract_failed": failed,
            "job_id": job["id"] if job else None, "job_status": job["status"] if job else None,
            "job_params": job["params"] if job else {}, "job_failures": job["failures"] if job else 0,
            "job_started_at": job["started_at"] if job else None, "job_finished_at": job["finished_at"] if job else None}


# --- Review ---

@app.get("/api/candidates")
def list_candidates(state: str = "pending", q: str = "", status: str = "", department: str = "", stage: str = ""):
    sql = ("SELECT id, email_key, review_state, fields->'name'->>'value' AS name,"
           " fields->'status'->>'value' AS status, fields->'department'->>'value' AS department,"
           " coalesce((fields->'engagement_paid'->>'value')::boolean, false) AS paid,"
           " coalesce(fields->'stage'->>'value', 'inquiry') AS stage,"
           " coalesce(jsonb_array_length(fields->'needs_check'->'value'), 0) AS needs_check,"
           " fields->'last_contact'->>'value' AS last_contact"
           " FROM candidates WHERE review_state = %s")
    args = [state]
    if q:
        sql += " AND (email_key ILIKE %s OR fields->'name'->>'value' ILIKE %s)"
        args += [f"%{q}%"] * 2
    if status:
        sql += " AND fields->'status'->>'value' = %s"
        args.append(status)
    if stage:
        sql += " AND coalesce(fields->'stage'->>'value', 'inquiry') = %s"
        args.append(stage)
    if department:
        sql += " AND fields->'department'->>'value' = %s"
        args.append(department)
    sql += " ORDER BY fields->'last_contact'->>'value' DESC NULLS LAST LIMIT 500"
    with db.connect() as conn:
        return conn.execute(sql, args).fetchall()


def _get(conn, cid: int, lock: bool = False) -> dict:
    row = conn.execute("SELECT * FROM candidates WHERE id = %s" + (" FOR UPDATE" if lock else ""), (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "candidate not found")
    return row


@app.get("/api/candidates/{cid}")
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


@app.post("/api/candidates/{cid}/approve")
def approve(cid: int, c: ClientIn):
    with db.connect() as conn:
        cand = _get(conn, cid, lock=True)
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
            "UPDATE candidates SET review_state='approved', reviewed=%s, reviewed_by=%s,"
            " reviewed_at=clock_timestamp(), updated_at=now() WHERE id=%s",
            (Jsonb(values), getuser(), cid))
    return {"ok": True}


@app.post("/api/publish")
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


@app.post("/api/candidates/{cid}/reject")
def reject(cid: int):
    with db.connect() as conn:
        _get(conn, cid)
        conn.execute("UPDATE candidates SET review_state='rejected', updated_at=now() WHERE id=%s", (cid,))
    return {"ok": True}


def _previous_target(conn, cand: dict) -> set[str]:
    return {_get(conn, cand["merged_into"])["email_key"]} if cand["merged_into"] else set()


@app.post("/api/candidates/{cid}/reopen")
def reopen(cid: int):
    with db.connect() as conn:
        cand = _get(conn, cid, lock=True)
        keys = {cand["email_key"]} | _previous_target(conn, cand)
        conn.execute("UPDATE candidates SET review_state='pending', merged_into=NULL, updated_at=now() WHERE id=%s", (cid,))
        pipeline.rebuild(conn, keys, load_rules(), datetime.now(timezone.utc))
    return {"ok": True}


class MergeIn(BaseModel):
    into_id: int


@app.post("/api/candidates/{cid}/merge")
def merge(cid: int, m: MergeIn):
    if cid == m.into_id:
        raise HTTPException(400, "cannot merge a record into itself")
    with db.connect() as conn:
        # Lock the source and target in id order.
        locked = {i: _get(conn, i, lock=True) for i in sorted((cid, m.into_id))}
        src, dst = locked[cid], locked[m.into_id]
        if dst["review_state"] == "merged":
            raise HTTPException(400, "target is itself merged; pick the record it was merged into")
        keys = {src["email_key"], dst["email_key"]} | _previous_target(conn, src)
        conn.execute("UPDATE candidates SET review_state='merged', merged_into=%s, updated_at=now() WHERE id=%s", (m.into_id, cid))
        pipeline.rebuild(conn, keys, load_rules(), datetime.now(timezone.utc))
    return {"ok": True}


@app.get("/api/raw/{item_id}")
def raw_item(item_id: int):
    """Full source item, for checking evidence."""
    with db.connect() as conn:
        row = conn.execute("SELECT id, source, mailbox, source_id, payload FROM raw_items WHERE id=%s",
                           (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "raw item not found")
    return row


@app.get("/api/rules")
def rules():
    return {"path": str(RULES_PATH), "text": RULES_PATH.read_text()}


@app.get("/")
def index():
    path = ROOT / "scraper" / "static" / "index.html"
    return FileResponse(path) if path.is_file() else PlainTextResponse("Open the review GUI at http://127.0.0.1:3000")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(env("GUI_PORT", "8765")))
