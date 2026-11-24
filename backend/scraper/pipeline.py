"""Scrape (fetch from Google) and extract (raw_items -> signals -> candidates) jobs."""

from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, Lock
import time
from time import monotonic

import psycopg
from psycopg.types.json import Jsonb

from . import db, master
from .db import JobStopped
from .config import env, env_int, env_list, load_rules
from .extract import extract_item
from .merge import build_candidates, from_json, resolve, to_json
from .sources import contacts, gmail, sheets


BATCH = 500
BACKLOG_SOURCE = "extract_backlog"
# Queued keys are rebuilt at most this often during a pass, and always at its end.
FLUSH_SECONDS = 60


def queue_rebuild(conn, keys, fresh=(), job_id=None) -> None:
    """Queue keys for a later rebuild, in the caller's transaction; fresh keys remember the scrape job."""
    fresh = set(fresh)
    rows = [(key, job_id if key in fresh else None) for key in sorted(set(keys) | fresh)]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO rebuild_queue (key, fresh_job) VALUES (%s,%s)"
            " ON CONFLICT (key) DO UPDATE SET fresh_job=coalesce(EXCLUDED.fresh_job, rebuild_queue.fresh_job)", rows)


def flush_rebuilds(conn, rules: dict) -> float:
    """Rebuild every queued key once and clear the queue, in one transaction. Returns seconds spent."""
    started = monotonic()
    queued = conn.execute("SELECT key, fresh_job FROM rebuild_queue FOR UPDATE").fetchall()
    if not queued:
        return 0.0
    keys = [r["key"] for r in queued]
    by_job: dict[int, set[str]] = {}
    for r in queued:
        if r["fresh_job"] is not None:
            by_job.setdefault(r["fresh_job"], set()).add(r["key"])
    rebuild(conn, keys, rules, datetime.now(timezone.utc), fresh_by_job=by_job)
    conn.execute("DELETE FROM rebuild_queue WHERE key=ANY(%s)", (keys,))
    return monotonic() - started


def rebuild_targets(keys, merged: dict[str, str]) -> dict[str, set[str]]:
    """All signal keys that contribute to each affected surviving person."""
    targets = {resolve(key, merged) for key in keys}
    groups = {key: {key} for key in targets}
    for key in merged:
        target = resolve(key, merged)
        if target in groups:
            groups[target].add(key)
    return groups


def rebuild(conn, keys, rules: dict, now: datetime, *, scrape_job_id=None, fresh_keys=(), fresh_by_job=None) -> None:
    conn.execute(master.LOCK)
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

    fresh_by_job = dict(fresh_by_job or {})
    if fresh_keys:
        fresh_by_job.setdefault(scrape_job_id, set()).update(fresh_keys)
    for job_id, fresh in fresh_by_job.items():
        conn.execute("UPDATE candidates SET pending_scrape_job=%s, pending_scraped_at=%s WHERE email_key=ANY(%s)",
                     (job_id, now, sorted({resolve(key, merged) for key in fresh})))
    master.rematch(conn, targets)


def _failure(log, count: int = 1) -> None:
    callback = getattr(log, "failure", None)
    if count and callback:
        callback(count)


def _progress(log, stage, done, total, *, conn=None, **fields):
    callback = getattr(log, "progress", None)
    if callback:
        callback(stage, done, total, conn=conn, **fields)


def run_scrape(sources: list[str], mailboxes: list[str], since: str, full: bool, log, *,
               checkpoint=lambda: None, done_mailboxes=(), since_explicit=False) -> None:
    parallel = env_int("SCRAPE_PARALLEL_MAILBOXES", 4, 1, 8, log)
    env_int("SCRAPE_MESSAGE_WORKERS", gmail.WORKERS, 1, 25, log)
    stopped = Event()
    finished_mailboxes = set()
    changed_ids = set()
    job_id = getattr(log, "job_id", None)
    progress_lock = Lock()
    resolved = []

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

    def mailbox_status(mb, status, error=None):
        callback = getattr(log, "mailbox_status", None)
        if callback:
            callback(mb, status, error)

    def on_changed(ids):
        with progress_lock:
            changed_ids.update(ids)

    def mailbox_task(mb):
        step = "connect"
        completed = False
        try:
            task_checkpoint()
            with db.connect() as conn:
                succeeded = True
                for name, fetch in (("gmail", lambda: gmail.fetch_mailbox(conn, mb, since, full, log,
                                                                         checkpoint=task_checkpoint, on_changed=on_changed,
                                                                         job_id=job_id, since_explicit=since_explicit)),
                                    ("contacts", lambda: contacts.fetch_contacts(conn, mb, log, full=full,
                                                                                 checkpoint=task_checkpoint, on_changed=on_changed,
                                                                                 job_id=job_id))):
                    if name not in sources:
                        continue
                    step = name
                    conn.commit()
                    task_checkpoint()
                    mailbox_status(mb, "running")
                    try:
                        fetch()
                    except JobStopped:
                        raise
                    except Exception as exc:
                        succeeded = False
                        mailbox_status(mb, "failed", repr(exc)[:500])
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
            completed = True
        except JobStopped:
            stopped.set()
            mailbox_status(mb, "pending")
        except Exception as exc:
            mailbox_status(mb, "failed", repr(exc)[:500])
            log(f"{mb} {step}: FAILED {exc!r}")
            _failure(log)
            completed = True
        finally:
            if completed:
                with progress_lock:
                    finished_mailboxes.add(mb)
                    _progress(log, "fetch", len(finished_mailboxes), len(resolved))

    if {"gmail", "contacts"} & set(sources):
        resolved = list(dict.fromkeys(mailboxes or gmail.list_mailboxes()))
        done = set(done_mailboxes)
        finished_mailboxes.update(done.intersection(resolved))
        _progress(log, "fetch", len(finished_mailboxes), len(resolved))
        seed_mailboxes = getattr(log, "mailboxes", None)
        if seed_mailboxes:
            seed_mailboxes(resolved, done)
        pending = [mb for mb in resolved if mb not in done]
        log(f"{len(pending)} mailboxes")
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            list(pool.map(mailbox_task, pending))
    else:
        _progress(log, "fetch", 0, 0)
    task_checkpoint()
    if "sheets" in sources:
        # Sheet ids are case-sensitive, so not env_list (which lower-cases).
        ids = [x.strip() for x in env("DRIVE_SHEET_IDS").split(",") if x.strip()]
        if ids:
            with db.connect() as conn:
                conn.commit()
                task_checkpoint()
                sheets.fetch_sheets(conn, ids, log, checkpoint=task_checkpoint, on_changed=on_changed, job_id=job_id)
        else:
            log("sheets: DRIVE_SHEET_IDS is empty, skipped")
    task_checkpoint()
    run_extract(log, checkpoint=task_checkpoint, raw_ids=changed_ids, scrape_job_id=job_id)


def count_pending(conn, *, eligible_only=False) -> int:
    criterion = " AND extract_error IS NULL" if eligible_only else ""
    return conn.execute(f"SELECT count(*) AS n FROM raw_items WHERE extracted_at IS NULL{criterion}").fetchone()["n"]


def run_extract(log, full: bool = False, *, keys=(), checkpoint=lambda: None, raw_ids=None, scrape_job_id=None) -> None:
    rules = load_rules()
    internal = set(env_list("INTERNAL_DOMAINS"))
    if not internal:
        raise RuntimeError("INTERNAL_DOMAINS is empty; staff would be listed as clients")
    qualifying_jobs = set()
    if scrape_job_id is not None:
        with db.connect() as conn:
            job = conn.execute("SELECT lineage FROM jobs WHERE id=%s AND name='scrape'", (scrape_job_id,)).fetchone()
            if job:
                lineage = job["lineage"]
                qualifying_jobs = {scrape_job_id, *lineage}
                if lineage:
                    # A stopped predecessor may have committed its fetch before it could extract.
                    raw_ids = set(raw_ids) | {row["id"] for row in conn.execute(
                        "SELECT id FROM raw_items WHERE extracted_at IS NULL AND extract_error IS NULL"
                        " AND last_job=ANY(%s)", (lineage,))}
    run_progress = _extract_pass(log, rules, internal, checkpoint, full=full, keys=keys, raw_ids=raw_ids,
                                 scrape_job_id=scrape_job_id, qualifying_jobs=qualifying_jobs)
    if raw_ids is not None:
        minutes = env_int("EXTRACT_BACKLOG_MINUTES", 20, 0, 1440)
        _extract_pass(log, rules, internal, checkpoint, backlog_deadline=monotonic() + minutes * 60,
                      run_progress=run_progress)
    checkpoint()
    _progress(log, "tag", 0, 1)
    with db.connect() as conn:
        master.rematch(conn)
        matched = master.counts(conn, master.available(conn))
        _progress(log, "tag", 1, 1, conn=conn)
    log(f"tag: {matched['email']} email, {matched['domain']} domain matches")
    succeeded, failed, _ = run_progress
    log(f"extract: done ({succeeded} items, {failed} failed)")


def _extract_pass(log, rules, internal, checkpoint, *, full=False, keys=(), raw_ids=None,
                  backlog_deadline=None, run_progress=None, scrape_job_id=None, qualifying_jobs=()):
    backlog = backlog_deadline is not None
    phase = "backlog" if backlog else "extract" if raw_ids is None else "scrape extract"
    started, rebuild_seconds = monotonic(), 0.0
    outcome = "finished"
    succeeded = failed = total_signals = last_id = 0
    rebuild_keys = set(keys)
    extract_total = None
    signal_data = ", data" if qualifying_jobs else ""
    pending_filter = "extracted_at IS NULL AND extract_error IS NULL"
    work_ids = sorted(raw_ids) if raw_ids is not None else None
    if work_ids is not None:
        pending_filter += " AND id=ANY(%s)"

    def progress(conn, done):
        if backlog:
            run_succeeded, run_failed, run_total = run_progress
            _progress(log, "extract", run_succeeded + run_failed, run_total, conn=conn,
                      backlog_done=done, backlog_total=extract_total)
        else:
            _progress(log, "extract", done, extract_total, conn=conn)

    def one_pass():
        nonlocal succeeded, failed, total_signals, last_id, full, extract_total, rebuild_seconds, outcome
        with db.connect() as conn:
            # Keys queued by an interrupted earlier pass are rebuilt before new work.
            rebuild_seconds += flush_rebuilds(conn, rules)
            conn.commit()
            last_flush = monotonic()
            # Also recover pending keys left without signals by an interrupted full reset.
            if not backlog:
                rebuild_keys.update(r["email_key"] for r in conn.execute(
                    "SELECT c.email_key FROM candidates c WHERE c.review_state='pending'"
                    " AND NOT EXISTS (SELECT 1 FROM signals s WHERE s.key=c.email_key)"))
            if full:
                rebuild_keys.update(r["key"] for r in conn.execute(
                    "SELECT key FROM signals UNION SELECT email_key AS key FROM candidates WHERE review_state='pending'"))
                conn.execute("DELETE FROM signals")
                conn.execute("UPDATE raw_items SET extracted_at=NULL, extract_error=NULL")
                conn.commit()
                full = False  # a retried pass must not wipe signals again
                reset_done = getattr(log, "full_reset_done", None)
                if reset_done:
                    reset_done()
                checkpoint()
            if extract_total is None:
                if backlog:
                    last_id = int(db.get_cursor(conn, BACKLOG_SOURCE, "") or 0)
                    # A row behind the cursor can become pending after an earlier interrupted scrape.
                    if last_id and not conn.execute(
                        f"SELECT 1 FROM raw_items WHERE {pending_filter} AND id>%s LIMIT 1", (last_id,)
                    ).fetchone():
                        first = conn.execute(f"SELECT min(id) AS id FROM raw_items WHERE {pending_filter}").fetchone()["id"]
                        if first is not None:
                            last_id = first - 1
                if work_ids is not None:
                    with conn.pipeline():
                        counts = [conn.execute(f"SELECT count(*) AS n FROM raw_items WHERE {pending_filter} AND id>%s",
                                               (work_ids[start:start + BATCH], last_id))
                                  for start in range(0, len(work_ids), BATCH)]
                    extract_total = sum(count.fetchone()["n"] for count in counts)
                elif backlog:
                    extract_total = conn.execute(f"SELECT count(*) AS n FROM raw_items WHERE {pending_filter} AND id>%s",
                                                 (last_id,)).fetchone()["n"]
                else:
                    extract_total = count_pending(conn, eligible_only=True)
            progress(conn, succeeded + failed)
            conn.commit()

            def stop_point():
                nonlocal rebuild_seconds
                try:
                    checkpoint()
                except JobStopped:
                    # A stop still leaves every committed batch's people rebuilt and tagged.
                    rebuild_seconds += flush_rebuilds(conn, rules)
                    conn.commit()
                    raise

            while succeeded + failed < extract_total:
                # shortcut: the budget is checked between durable batches, add in-batch cancellation if a hard cutoff is needed
                if backlog and monotonic() >= backlog_deadline:
                    outcome = "time limit reached"
                    break
                batch_ids = []
                if work_ids is not None:
                    start = bisect_right(work_ids, last_id)
                    batch_ids = work_ids[start:start + BATCH]
                    if not batch_ids:
                        break
                filters = (batch_ids,) if batch_ids else ()
                rows = conn.execute(
                    f"SELECT id, source, payload, last_job FROM raw_items WHERE {pending_filter} AND id>%s"
                    " ORDER BY id LIMIT %s FOR UPDATE", (*filters, last_id, BATCH)).fetchall()
                if not rows:
                    if batch_ids:
                        conn.commit()
                        last_id = batch_ids[-1]
                        stop_point()
                        continue
                    break
                ids = [r["id"] for r in rows]
                old_signals = {}
                for r in conn.execute(f"SELECT raw_item_id, key{signal_data} FROM signals WHERE raw_item_id=ANY(%s)", (ids,)):
                    old_signals.setdefault(r["raw_item_id"], {}).setdefault(r["key"], []).append(r.get("data"))
                affected, fresh, successful, signals = set(), set(), [], []
                for row in rows:
                    try:
                        extracted = [to_json(s) for s in extract_item(row, rules, internal)]
                    except JobStopped:
                        raise
                    except Exception as exc:
                        conn.execute("UPDATE raw_items SET extracted_at=now(), extract_error=%s WHERE id=%s",
                                     (repr(exc), row["id"]))
                        continue
                    successful.append(row["id"])
                    previous = old_signals.get(row["id"], {})
                    affected.update(previous)
                    affected.update(s["key"] for s in extracted)
                    if row["last_job"] in qualifying_jobs:
                        for signal in extracted:
                            prior = previous.get(signal["key"], [])
                            if signal in prior:
                                prior.remove(signal)
                            else:
                                fresh.add(signal["key"])
                    signals.extend((row["id"], s["key"], Jsonb(s)) for s in extracted)
                conn.execute("DELETE FROM signals WHERE raw_item_id=ANY(%s)", (successful,))
                with conn.cursor() as cur:
                    cur.executemany("INSERT INTO signals (raw_item_id, key, data) VALUES (%s,%s,%s)", signals)
                conn.execute("UPDATE raw_items SET extracted_at=now(), extract_error=NULL WHERE id=ANY(%s)", (successful,))
                if affected:
                    # Queued with the signals (same transaction); rebuilt together about once a minute.
                    queue_rebuild(conn, affected, fresh, scrape_job_id)
                if monotonic() - last_flush >= FLUSH_SECONDS:
                    rebuild_seconds += flush_rebuilds(conn, rules)
                    last_flush = monotonic()
                if backlog:
                    db.set_cursor(conn, BACKLOG_SOURCE, "", str(rows[-1]["id"]))
                progress(conn, succeeded + failed + len(rows))
                # Signals, markers and candidates commit together; a failed batch is retried.
                conn.commit()
                _failure(log, len(rows) - len(successful))
                failed += len(rows) - len(successful)
                succeeded += len(successful)
                total_signals += len(signals)
                last_id = batch_ids[-1] if batch_ids else rows[-1]["id"]
                done = succeeded + failed
                elapsed = max(monotonic() - started, 0.001)
                label = f"backlog {done}/{extract_total}" if backlog else phase
                log(f"{label}: {succeeded} items, {total_signals} signals, {failed} failed"
                    f" ({done / elapsed:.1f} rows/s, {rebuild_seconds:.1f}s rebuilding)")
                stop_point()
            if rebuild_keys:
                queue_rebuild(conn, rebuild_keys)
            rebuild_seconds += flush_rebuilds(conn, rules)
            conn.commit()

    drops = 0
    try:
        if backlog:
            checkpoint()
        while True:
            done_before = succeeded + failed
            try:
                one_pass()
                break
            except psycopg.OperationalError as exc:
                # Only the uncommitted batch is lost; a new pass resumes from extracted_at markers.
                drops = drops + 1 if succeeded + failed == done_before else 1
                if drops > 3:
                    raise
                log(f"{phase}: database connection lost ({exc.__class__.__name__}); reconnecting and retrying the batch")
                time.sleep(5 * drops)
    except JobStopped as exc:
        outcome = f"stopped at checkpoint ({exc or 'stop requested or job interrupted'})"
        raise
    except BaseException as exc:
        outcome = type(exc).__name__
        raise
    finally:
        elapsed = monotonic() - started
        throughput = (succeeded + failed) / max(elapsed, 0.001)
        log(f"{phase}: pass {outcome}; {succeeded + failed} attempted in {elapsed:.1f}s"
            f" ({throughput:.1f} rows/s, {rebuild_seconds:.1f}s rebuilding)")
    return succeeded, failed, extract_total
