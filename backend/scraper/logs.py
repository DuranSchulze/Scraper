"""Scraper history for the Logs page and its CSV/XLSX exports. Dates are Philippine (Asia/Manila) calendar days."""

import csv
import io
from datetime import date, datetime, timedelta, timezone

from . import pipeline

TZ = "Asia/Manila"
PHT = timezone(timedelta(hours=8))
# Inclusive Manila day range [from, to] as instants: from 00:00 PHT up to the next day after `to`.
RANGE = "{col} >= (%(from)s::date)::timestamp AT TIME ZONE 'Asia/Manila'" \
        " AND {col} < (%(to)s::date + 1)::timestamp AT TIME ZONE 'Asia/Manila'"


def bounds(date_from: str, date_to: str) -> dict:
    """Validated YYYY-MM-DD params; defaults to the last 30 Manila days."""
    today = datetime.now(PHT).date()
    to = date.fromisoformat(date_to) if date_to else today
    start = date.fromisoformat(date_from) if date_from else to - timedelta(days=29)
    if start > to:
        raise ValueError("from is after to")
    return {"from": start.isoformat(), "to": to.isoformat()}


def ph(ts) -> str:
    """An instant as 'YYYY-MM-DD HH:MM' Philippine time, for exports."""
    return ts.astimezone(PHT).strftime("%Y-%m-%d %H:%M") if ts else ""


def summary(conn, r: dict, schedule: dict) -> dict:
    stored = {row["source"]: row["n"] for row in conn.execute("SELECT source, count(*) AS n FROM raw_items GROUP BY source")}
    fetched = {row["source"]: row["n"] for row in conn.execute(
        f"SELECT source, count(*) AS n FROM raw_items WHERE {RANGE.format(col='fetched_at')} GROUP BY source", r)}
    runs = conn.execute(
        "SELECT count(*) AS total,"
        " count(*) FILTER (WHERE params->>'auto'='true') AS auto,"
        " count(*) FILTER (WHERE (params->>'full')::boolean) AS full,"
        " count(*) FILTER (WHERE completed) AS completed,"
        " count(*) FILTER (WHERE status IN ('failed','interrupted','stopped')) AS unfinished"
        f" FROM jobs WHERE name='scrape' AND {RANGE.format(col='started_at')}", r).fetchone()
    last = conn.execute("SELECT id, finished_at FROM jobs WHERE completed ORDER BY finished_at DESC, id DESC LIMIT 1").fetchone()
    last_full = conn.execute("SELECT id, finished_at FROM jobs WHERE completed AND (params->>'full')::boolean"
                             " ORDER BY finished_at DESC, id DESC LIMIT 1").fetchone()
    published = conn.execute(f"SELECT count(*) AS n FROM candidates WHERE {RANGE.format(col='first_published_at')}", r).fetchone()["n"]
    states = {row["review_state"]: row["n"] for row in conn.execute("SELECT review_state, count(*) AS n FROM candidates GROUP BY 1")}
    return {
        "range": r,
        "stored": stored,
        "fetched": fetched,
        "runs": runs,
        "last_complete_scrape_at": last["finished_at"] if last else None,
        "last_full_scrape_at": last_full["finished_at"] if last_full else None,
        "next_run_at": schedule.get("next_run_at"),
        "schedule": {"enabled": schedule.get("enabled"), "interval_minutes": schedule.get("interval_minutes")},
        "backlog_pending": pipeline.count_pending(conn, eligible_only=True),
        "published": published,
        "clients_total": conn.execute("SELECT count(*) AS n FROM clients").fetchone()["n"],
        "candidates": states,
    }


JOBS_SQL = (
    "SELECT j.id, j.name, j.status, j.completed, j.started_at, j.finished_at, j.failures, j.error,"
    " coalesce(j.params->>'auto','false')::boolean AS auto, coalesce(j.params->>'full','false')::boolean AS full,"
    " extract(epoch FROM coalesce(j.finished_at, j.heartbeat_at) - j.started_at)::bigint AS seconds,"
    " count(m.*) FILTER (WHERE m.status='done') AS mailboxes_done, count(m.*) AS mailboxes,"
    " coalesce(sum(m.fetched),0)::bigint AS emails_fetched"
    " FROM jobs j LEFT JOIN job_mailboxes m ON m.job_id=j.id"
    f" WHERE {RANGE.format(col='j.started_at')} GROUP BY j.id ORDER BY j.started_at DESC, j.id DESC")


def jobs(conn, r: dict, limit: int | None = None, offset: int = 0) -> tuple[list[dict], int]:
    total = conn.execute(f"SELECT count(*) AS n FROM jobs WHERE {RANGE.format(col='started_at')}", r).fetchone()["n"]
    page = " LIMIT %(limit)s OFFSET %(offset)s" if limit else ""
    return conn.execute(JOBS_SQL + page, {**r, "limit": limit, "offset": offset}).fetchall(), total


def daily(conn, r: dict) -> list[dict]:
    """One row per Manila day in the range, newest first, including empty days."""
    return conn.execute(
        "WITH days AS (SELECT d::date AS day FROM generate_series(%(from)s::date, %(to)s::date, interval '1 day') d),"
        " f AS (SELECT (fetched_at AT TIME ZONE 'Asia/Manila')::date AS day,"
        "        count(*) FILTER (WHERE source='gmail') AS emails, count(*) FILTER (WHERE source='contacts') AS contacts,"
        "        count(*) FILTER (WHERE source='sheets') AS sheet_rows"
        f"       FROM raw_items WHERE {RANGE.format(col='fetched_at')} GROUP BY 1),"
        " j AS (SELECT (started_at AT TIME ZONE 'Asia/Manila')::date AS day, count(*) AS runs,"
        "        count(*) FILTER (WHERE completed) AS completed"
        f"       FROM jobs WHERE name='scrape' AND {RANGE.format(col='started_at')} GROUP BY 1),"
        " p AS (SELECT (first_published_at AT TIME ZONE 'Asia/Manila')::date AS day, count(*) AS published"
        f"       FROM candidates WHERE {RANGE.format(col='first_published_at')} GROUP BY 1)"
        " SELECT days.day, coalesce(f.emails,0) AS emails, coalesce(f.contacts,0) AS contacts,"
        " coalesce(f.sheet_rows,0) AS sheet_rows, coalesce(j.runs,0) AS runs, coalesce(j.completed,0) AS completed_runs,"
        " coalesce(p.published,0) AS published"
        " FROM days LEFT JOIN f USING (day) LEFT JOIN j USING (day) LEFT JOIN p USING (day) ORDER BY days.day DESC", r).fetchall()


PUBLISHED_SQL = (
    "SELECT c.id, c.email_key AS email, coalesce(c.reviewed->>'name', c.fields->'name'->>'value') AS name,"
    " coalesce(c.reviewed->>'company', c.fields->'company'->>'value') AS company, c.client_id,"
    " c.first_published_at, c.published_at, c.reviewed_by"
    f" FROM candidates c WHERE {RANGE.format(col='c.first_published_at')} ORDER BY c.first_published_at DESC, c.id DESC")


def published(conn, r: dict, limit: int | None = None, offset: int = 0) -> tuple[list[dict], int]:
    total = conn.execute(f"SELECT count(*) AS n FROM candidates WHERE {RANGE.format(col='first_published_at')}", r).fetchone()["n"]
    page = " LIMIT %(limit)s OFFSET %(offset)s" if limit else ""
    return conn.execute(PUBLISHED_SQL + page, {**r, "limit": limit, "offset": offset}).fetchall(), total


# Export columns: (header, row -> value). Timestamps become Philippine time strings.
COLUMNS = {
    "jobs": [("Job", lambda j: j["id"]), ("Type", lambda j: j["name"]),
             ("Trigger", lambda j: "Auto" if j["auto"] else "Manual"), ("Full rescan", lambda j: "Yes" if j["full"] else "No"),
             ("Status", lambda j: j["status"]), ("Completed", lambda j: "Yes" if j["completed"] else "No"),
             ("Started (PHT)", lambda j: ph(j["started_at"])), ("Finished (PHT)", lambda j: ph(j["finished_at"])),
             ("Duration (min)", lambda j: round((j["seconds"] or 0) / 60, 1)),
             ("Mailboxes done", lambda j: j["mailboxes_done"]), ("Mailboxes", lambda j: j["mailboxes"]),
             ("Emails fetched", lambda j: j["emails_fetched"]), ("Failures", lambda j: j["failures"]),
             ("Error", lambda j: j["error"] or "")],
    "daily": [("Date (PHT)", lambda d: d["day"].isoformat()), ("Emails scraped", lambda d: d["emails"]),
              ("Contacts", lambda d: d["contacts"]), ("Sheet rows", lambda d: d["sheet_rows"]),
              ("Scrape runs", lambda d: d["runs"]), ("Completed runs", lambda d: d["completed_runs"]),
              ("Added to directory", lambda d: d["published"])],
    "published": [("Record", lambda p: p["id"]), ("Name", lambda p: p["name"] or ""), ("Email", lambda p: p["email"]),
                  ("Company", lambda p: p["company"] or ""), ("Client id", lambda p: p["client_id"]),
                  ("First added (PHT)", lambda p: ph(p["first_published_at"])),
                  ("Last published (PHT)", lambda p: ph(p["published_at"])), ("Approved by", lambda p: p["reviewed_by"] or "")],
}


def table_rows(conn, table: str, r: dict) -> list[list]:
    rows = {"jobs": lambda: jobs(conn, r)[0], "daily": lambda: daily(conn, r), "published": lambda: published(conn, r)[0]}[table]()
    return [[h for h, _ in COLUMNS[table]]] + [[get(row) for _, get in COLUMNS[table]] for row in rows]


def to_csv(rows: list[list]) -> bytes:
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return ("﻿" + out.getvalue()).encode("utf-8")  # BOM so Excel reads UTF-8 names correctly


def summary_rows(s: dict) -> list[list]:
    runs = s["runs"]
    return [["Field", "Value"],
            ["Range (PHT)", f"{s['range']['from']} to {s['range']['to']}"],
            ["Exported (PHT)", ph(datetime.now(timezone.utc))],
            ["Last complete scrape (PHT)", ph(s["last_complete_scrape_at"])],
            ["Last full rescan (PHT)", ph(s["last_full_scrape_at"])],
            ["Next automatic run (PHT)", ph(s["next_run_at"]) or "Not scheduled"],
            ["Scrape runs in range", runs["total"]], ["Automatic runs", runs["auto"]], ["Full rescans", runs["full"]],
            ["Completed runs", runs["completed"]], ["Stopped, failed or interrupted", runs["unfinished"]],
            ["Emails scraped in range", s["fetched"].get("gmail", 0)], ["Contacts scraped in range", s["fetched"].get("contacts", 0)],
            ["Sheet rows scraped in range", s["fetched"].get("sheets", 0)],
            ["Added to directory in range", s["published"]], ["Clients in directory", s["clients_total"]],
            ["Emails stored (all time)", s["stored"].get("gmail", 0)], ["Backlog waiting for extraction", s["backlog_pending"]]]


def to_xlsx(sheets: dict[str, list[list]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width = min(48, max(10, *(len(str(c.value or "")) + 2 for c in col)))
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
