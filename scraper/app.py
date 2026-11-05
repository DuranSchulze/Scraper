"""Local review GUI. Run: uv run python -m scraper.app  (binds 127.0.0.1 only)."""

import threading
from collections import deque
from datetime import datetime, timezone
from getpass import getuser
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import db, pipeline
from .config import RULES_PATH, ROOT, env, load_rules

app = FastAPI(title="Workspace Scraper", docs_url=None, redoc_url=None, openapi_url=None)
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


# --- Background job (one at a time) ---

JOB = {"running": False, "name": "", "error": None, "log": deque(maxlen=500)}
JOB_LOCK = threading.Lock()


def log(msg: str) -> None:
    JOB["log"].append(f"{datetime.now():%H:%M:%S} {msg}")


def start_job(name: str, fn) -> None:
    with JOB_LOCK:
        if JOB["running"]:
            raise HTTPException(409, f"'{JOB['name']}' is still running")
        JOB.update(running=True, name=name, error=None)
        JOB["log"].clear()

    def run():
        try:
            fn()
            log(f"{name}: finished")
        except Exception as e:
            JOB["error"] = repr(e)
            log(f"ERROR: {e!r}")
        finally:
            JOB["running"] = False

    threading.Thread(target=run, daemon=True).start()


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
    start_job("scrape", lambda: pipeline.run_scrape(body.sources, mailboxes, since, body.full, log))
    return {"started": True}


class ExtractIn(BaseModel):
    full: bool = False


@app.post("/api/extract")
def extract(body: ExtractIn):
    start_job("extract", lambda: pipeline.run_extract(log, full=body.full))
    return {"started": True}


@app.get("/api/status")
def status():
    with db.connect() as conn:
        counts = {r["review_state"]: r["n"] for r in conn.execute(
            "SELECT review_state, count(*) AS n FROM candidates GROUP BY review_state")}
        raw = {r["source"]: r["n"] for r in conn.execute(
            "SELECT source, count(*) AS n FROM raw_items GROUP BY source")}
        ready = conn.execute(
            "SELECT count(*) AS n FROM candidates WHERE review_state='approved'"
            " AND (published_at IS NULL OR reviewed_at>published_at)").fetchone()["n"]
        failed = conn.execute("SELECT count(*) AS n FROM raw_items WHERE extract_error IS NOT NULL").fetchone()["n"]
    return {"running": JOB["running"], "job": JOB["name"], "error": JOB["error"],
            "log": list(JOB["log"]), "candidates": counts, "raw_items": raw,
            "ready_to_publish": ready, "extract_failed": failed}


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
    first_contact: datetime | None = None
    last_contact: datetime | None = None
    notes: str | None = None


@app.post("/api/candidates/{cid}/approve")
def approve(cid: int, c: ClientIn):
    with db.connect() as conn:
        cand = _get(conn, cid, lock=True)
        if cand["review_state"] == "merged":
            raise HTTPException(409, "reopen the merged record before approving it")
        conn.execute(
            "UPDATE candidates SET review_state='approved', reviewed=%s, reviewed_by=%s,"
            " reviewed_at=clock_timestamp(), updated_at=now() WHERE id=%s",
            (Jsonb(c.model_dump(mode="json")), getuser(), cid))
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
            vals = (c.name, c.emails, c.phones, c.company, c.status, c.services, c.department,
                    c.engagement_paid, c.first_contact, c.last_contact, c.notes, cand["reviewed_by"],
                    cand["reviewed_at"], c.stage, Jsonb([a.model_dump(mode="json") for a in c.amounts]))
            if cand["client_id"] is not None:
                conn.execute(
                    "UPDATE clients SET name=%s, emails=%s, phones=%s, company=%s, status=%s, services=%s,"
                    " department=%s, engagement_paid=%s, first_contact=%s, last_contact=%s, notes=%s,"
                    " approved_by=%s, approved_at=%s, stage=%s, amounts=%s, updated_at=now() WHERE id=%s",
                    (*vals, cand["client_id"]))
                client_id = cand["client_id"]
                updated += 1
            else:
                client_id = conn.execute(
                    "INSERT INTO clients (name, emails, phones, company, status, services, department,"
                    " engagement_paid, first_contact, last_contact, notes, approved_by, approved_at, stage, amounts)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", vals).fetchone()["id"]
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
    return FileResponse(ROOT / "scraper" / "static" / "index.html")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(env("GUI_PORT", "8765")))
