"""Scrape (fetch from Google) and extract (raw_items -> signals -> candidates) jobs."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, Lock

from psycopg.types.json import Jsonb

from . import db
from .db import JobStopped
from .config import env, env_int, env_list, load_rules
from .extract import extract_item
from .merge import build_candidates, from_json, resolve, to_json
from .sources import contacts, gmail, sheets


BATCH = 500


def rebuild_targets(keys, merged: dict[str, str]) -> dict[str, set[str]]:
    """All signal keys that contribute to each affected surviving person."""
    targets = {resolve(key, merged) for key in keys}
    groups = {key: {key} for key in targets}
    for key in merged:
        target = resolve(key, merged)
        if target in groups:
            groups[target].add(key)
    return groups


def rebuild(conn, keys, rules: dict, now: datetime) -> None:
    merged = {r["email_key"]: r["target"] for r in conn.execute(
        "SELECT c.email_key, t.email_key AS target FROM candidates c"
        " JOIN candidates t ON t.id=c.merged_into WHERE c.review_state='merged'")}
    groups = rebuild_targets(keys, merged)
    targets = sorted(groups)
    for offset in range(0, len(targets), BATCH):
        chunk = targets[offset:offset + BATCH]
        source_keys = sorted({key for target in chunk for key in groups[target]})
        signals = [from_json(r["data"]) for r in conn.execute(
            "SELECT data FROM signals WHERE key=ANY(%s) ORDER BY id", (source_keys,))]
        candidates = build_candidates(signals, rules, now, merged)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO candidates (email_key, fields, evidence) VALUES (%s,%s,%s)"
                " ON CONFLICT (email_key) DO UPDATE SET fields=EXCLUDED.fields,"
                " evidence=EXCLUDED.evidence, updated_at=now() WHERE candidates.review_state='pending'",
                [(c["key"], Jsonb(c["fields"]), Jsonb(c["evidence"])) for c in candidates.values()])
        absent = [key for key in chunk if key not in candidates]
        # Keep reviewer edits, published identities and surviving merge links.
        conn.execute(
            "DELETE FROM candidates c WHERE c.review_state='pending' AND c.email_key=ANY(%s)"
            " AND c.reviewed IS NULL AND c.client_id IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM candidates src WHERE src.merged_into=c.id)", (absent,))


def _failure(log, count: int = 1) -> None:
    callback = getattr(log, "failure", None)
    if count and callback:
        callback(count)


def run_scrape(sources: list[str], mailboxes: list[str], since: str, full: bool, log, *,
               checkpoint=lambda: None, done_mailboxes=()) -> None:
    parallel = env_int("SCRAPE_PARALLEL_MAILBOXES", 4, 1, 8, log)
    env_int("SCRAPE_MESSAGE_WORKERS", gmail.WORKERS, 1, 25, log)
    stopped, extract_lock = Event(), Lock()

    def task_checkpoint():
        if stopped.is_set():
            raise JobStopped()
        try:
            checkpoint()
        except JobStopped:
            stopped.set()
            raise
        if stopped.is_set():
            raise JobStopped()

    def mailbox_task(mb):
        step = "connect"
        try:
            task_checkpoint()
            with db.connect() as conn:
                succeeded = True
                for name, fetch in (("gmail", lambda: gmail.fetch_mailbox(conn, mb, since, full, log, checkpoint=task_checkpoint)),
                                    ("contacts", lambda: contacts.fetch_contacts(conn, mb, log, full=full, checkpoint=task_checkpoint))):
                    if name not in sources:
                        continue
                    step = name
                    conn.commit()
                    task_checkpoint()
                    try:
                        fetch()
                    except JobStopped:
                        raise
                    except Exception as exc:
                        succeeded = False
                        conn.rollback()
                        log(f"{mb} {name}: FAILED {exc!r}")
                        _failure(log)
                if succeeded:
                    step = "mailbox_done"
                    conn.commit()
                    mailbox_done = getattr(log, "mailbox_done", None)
                    if mailbox_done:
                        mailbox_done(mb)
                conn.commit()
                step = "extract"
                task_checkpoint()
                while not extract_lock.acquire(timeout=1):
                    task_checkpoint()
                try:
                    task_checkpoint()
                    run_extract(log, checkpoint=task_checkpoint)
                finally:
                    extract_lock.release()
        except JobStopped:
            stopped.set()
        except Exception as exc:
            log(f"{mb} {step}: FAILED {exc!r}")
            _failure(log)

    if {"gmail", "contacts"} & set(sources):
        resolved = list(dict.fromkeys(mailboxes or gmail.list_mailboxes()))
        done = set(done_mailboxes)
        pending = [mb for mb in resolved if mb not in done]
        log(f"{len(pending)} mailboxes")
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            list(pool.map(mailbox_task, pending))
    task_checkpoint()
    if "sheets" in sources:
        # Sheet ids are case-sensitive, so not env_list (which lower-cases).
        ids = [x.strip() for x in env("DRIVE_SHEET_IDS").split(",") if x.strip()]
        if ids:
            with db.connect() as conn:
                conn.commit()
                task_checkpoint()
                sheets.fetch_sheets(conn, ids, log, checkpoint=task_checkpoint)
        else:
            log("sheets: DRIVE_SHEET_IDS is empty, skipped")
    task_checkpoint()
    run_extract(log, checkpoint=task_checkpoint)


def run_extract(log, full: bool = False, *, keys=(), checkpoint=lambda: None) -> None:
    rules = load_rules()
    internal = set(env_list("INTERNAL_DOMAINS"))
    if not internal:
        raise RuntimeError("INTERNAL_DOMAINS is empty; staff would be listed as clients")
    succeeded = failed = total_signals = last_id = 0
    with db.connect() as conn:
        # Also recover pending keys left without signals by an interrupted full reset.
        rebuild_keys = set(keys) | {r["email_key"] for r in conn.execute(
            "SELECT c.email_key FROM candidates c WHERE c.review_state='pending'"
            " AND NOT EXISTS (SELECT 1 FROM signals s WHERE s.key=c.email_key)")}
        if full:
            rebuild_keys.update(r["key"] for r in conn.execute(
                "SELECT key FROM signals UNION SELECT email_key AS key FROM candidates WHERE review_state='pending'"))
            conn.execute("DELETE FROM signals")
            conn.execute("UPDATE raw_items SET extracted_at=NULL, extract_error=NULL")
            conn.commit()
            reset_done = getattr(log, "full_reset_done", None)
            if reset_done:
                reset_done()
            checkpoint()
        while True:
            rows = conn.execute(
                "SELECT id, source, payload FROM raw_items WHERE extracted_at IS NULL AND id>%s"
                " ORDER BY id LIMIT %s", (last_id, BATCH)).fetchall()
            if not rows:
                break
            ids = [r["id"] for r in rows]
            old_keys = {}
            for r in conn.execute("SELECT raw_item_id, key FROM signals WHERE raw_item_id=ANY(%s)", (ids,)):
                old_keys.setdefault(r["raw_item_id"], set()).add(r["key"])
            affected, successful, signals = set(), [], []
            for row in rows:
                try:
                    extracted = [to_json(s) for s in extract_item(row, rules, internal)]
                except JobStopped:
                    raise
                except Exception as exc:
                    conn.execute("UPDATE raw_items SET extract_error=%s WHERE id=%s", (repr(exc), row["id"]))
                    failed += 1
                    continue
                successful.append(row["id"])
                affected.update(old_keys.get(row["id"], set()))
                affected.update(s["key"] for s in extracted)
                signals.extend((row["id"], s["key"], Jsonb(s)) for s in extracted)
            conn.execute("DELETE FROM signals WHERE raw_item_id=ANY(%s)", (successful,))
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO signals (raw_item_id, key, data) VALUES (%s,%s,%s)", signals)
            conn.execute("UPDATE raw_items SET extracted_at=now(), extract_error=NULL WHERE id=ANY(%s)", (successful,))
            rebuild(conn, affected, rules, datetime.now(timezone.utc))
            # Signals, markers and candidates commit together; a failed batch is retried.
            conn.commit()
            _failure(log, len(rows) - len(successful))
            succeeded += len(successful)
            total_signals += len(signals)
            last_id = rows[-1]["id"]
            log(f"extract: {succeeded} items, {total_signals} signals, {failed} failed")
            checkpoint()
        if rebuild_keys:
            rebuild(conn, rebuild_keys, rules, datetime.now(timezone.utc))
        conn.commit()
    checkpoint()
    log(f"extract: done ({succeeded} items, {failed} failed)")
