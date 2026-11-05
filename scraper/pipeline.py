"""Scrape (fetch from Google) and extract (raw_items -> candidates) jobs."""

from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from . import db
from .config import env, env_list, load_rules
from .extract import extract_item
from .merge import build_candidates
from .sources import contacts, gmail, sheets


def run_scrape(sources: list[str], mailboxes: list[str], since: str, full: bool, log) -> None:
    with db.connect() as conn:
        if {"gmail", "contacts"} & set(sources):
            mailboxes = mailboxes or gmail.list_mailboxes()
            log(f"{len(mailboxes)} mailboxes")
            for mb in mailboxes:
                for name, fetch in (("gmail", lambda: gmail.fetch_mailbox(conn, mb, since, full, log)),
                                    ("contacts", lambda: contacts.fetch_contacts(conn, mb, log))):
                    if name not in sources:
                        continue
                    try:
                        fetch()
                    except Exception as e:  # one bad mailbox (no license, no Gmail) must not stop the run
                        conn.rollback()
                        log(f"{mb} {name}: FAILED {e!r}")
        if "sheets" in sources:
            # Sheet ids are case-sensitive, so not env_list (which lower-cases).
            ids = [x.strip() for x in env("DRIVE_SHEET_IDS").split(",") if x.strip()]
            if ids:
                sheets.fetch_sheets(conn, ids, log)
            else:
                log("sheets: DRIVE_SHEET_IDS is empty, skipped")
    run_extract(log)


def run_extract(log) -> None:
    rules = load_rules()
    internal = set(env_list("INTERNAL_DOMAINS"))
    if not internal:
        raise RuntimeError("INTERNAL_DOMAINS is empty; staff would be listed as clients")
    signals = []
    with db.connect() as conn:
        with conn.cursor(name="raw_scan") as cur:  # server-side cursor: bodies are not all held in memory
            cur.itersize = 2000
            cur.execute("SELECT id, source, payload FROM raw_items")
            for row in cur:
                signals += extract_item(row, rules, internal)
        merged = {r["email_key"]: r["target"] for r in conn.execute(
            "SELECT c.email_key, t.email_key AS target FROM candidates c"
            " JOIN candidates t ON t.id = c.merged_into WHERE c.review_state = 'merged'")}
        cands = build_candidates(signals, rules, datetime.now(timezone.utc), merged)
        log(f"extract: {len(signals)} signals -> {len(cands)} people")
        with conn.cursor() as cur:
            # Reviewed records (approved/rejected/merged) are never overwritten.
            cur.executemany(
                "INSERT INTO candidates (email_key, fields, evidence) VALUES (%s, %s, %s)"
                " ON CONFLICT (email_key) DO UPDATE SET fields = EXCLUDED.fields,"
                " evidence = EXCLUDED.evidence, updated_at = now()"
                " WHERE candidates.review_state = 'pending'",
                [(c["key"], Jsonb(c["fields"]), Jsonb(c["evidence"])) for c in cands.values()],
            )
        conn.commit()
    log("extract: done")
