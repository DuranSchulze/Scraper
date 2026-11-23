"""Neon Postgres access. Run `npm run db push` to apply migrations."""

import sys

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import ROOT, env

MIGRATIONS_DIR = ROOT / "migrations"


class JobStopped(Exception):
    pass


def connect() -> psycopg.Connection:
    url = env("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    # Keepalives stop NAT/proxies from silently dropping a connection that is busy in Python for minutes.
    return psycopg.connect(url, row_factory=dict_row, keepalives=1, keepalives_idle=30,
                           keepalives_interval=10, keepalives_count=5)


def pending_migrations(applied: set[str]) -> list:
    return [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.name not in applied]


def migrate() -> list[str]:
    """Apply each unapplied migration file in its own transaction. Returns names applied."""
    done = []
    with connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        conn.commit()
        applied = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
        for path in pending_migrations(applied):
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            done.append(path.name)
    return done


# --- Helpers used by the scraper. The caller commits. ---

def get_cursor(conn, source: str, mailbox: str) -> str | None:
    row = conn.execute(
        "SELECT cursor FROM sync_state WHERE source = %s AND mailbox = %s", (source, mailbox)
    ).fetchone()
    return row["cursor"] if row else None


def set_cursor(conn, source: str, mailbox: str, cursor: str) -> None:
    conn.execute(
        "INSERT INTO sync_state (source, mailbox, cursor) VALUES (%s, %s, %s)"
        " ON CONFLICT (source, mailbox) DO UPDATE SET cursor = EXCLUDED.cursor, updated_at = now()",
        (source, mailbox, cursor),
    )


def missing_ids(conn, source: str, mailbox: str, ids: list[str], min_version: int = 1, *, refresh=()) -> list[str]:
    """Ids not yet stored (or stored with an older payload version), in input order."""
    have = {
        r["source_id"]
        for r in conn.execute(
            "SELECT source_id FROM raw_items WHERE source = %s AND mailbox = %s AND source_id = ANY(%s)"
            " AND coalesce((payload->>'v')::int, 1) >= %s AND NOT (source_id=ANY(%s))"
            " UNION SELECT source_id FROM gone_items WHERE source=%s AND mailbox=%s AND source_id=ANY(%s)",
            (source, mailbox, ids, min_version, list(refresh), source, mailbox, ids),
        )
    }
    return [i for i in ids if i not in have]


def outdated_gmail_ids(conn, mailbox: str, min_version: int) -> list[str]:
    return [r["source_id"] for r in conn.execute(
        "SELECT r.source_id FROM raw_items r WHERE r.source = 'gmail' AND r.mailbox = %s"
        " AND coalesce((r.payload->>'v')::int, 1) < %s"
        " AND NOT EXISTS (SELECT 1 FROM gone_items g WHERE g.source=r.source"
        " AND g.mailbox=r.mailbox AND g.source_id=r.source_id) ORDER BY r.id",
        (mailbox, min_version),
    )]


UPSERT_RAW = (
    "INSERT INTO raw_items (source, mailbox, source_id, payload) VALUES (%s, %s, %s, %s)"
    " ON CONFLICT (source, mailbox, source_id)"
    # A changed payload must be extracted again.
    " DO UPDATE SET payload = EXCLUDED.payload, fetched_at = now(), extracted_at = NULL, extract_error = NULL"
)


def _gmail_material_sql(payload):
    def pick(path, default):
        return f"coalesce({payload}{path}, '{default}'::jsonb)"

    fields = [f"{payload} ? 'direction'", f"{payload}->'direction'", f"{payload}->'internal_date'",
              f"jsonb_typeof({payload}->'headers')"]
    fields += [pick(f"->'{field}'", default) for field, default in (
        ("body", '\"\"'), ("thread_id", '\"\"'), ("mailbox", '\"\"'), ("labels", "[]"), ("attachment_text", "{}"))]
    fields += [pick(f"#>'{{headers,{header}}}'", '\"no\"' if header == "auto-submitted" else '\"\"')
               for header in ("from", "to", "cc", "delivered-to", "subject", "list-unsubscribe", "list-id",
                              "precedence", "auto-submitted")]
    attachments = pick("->'attachments'", "[]")
    fields.append(
        f"CASE WHEN jsonb_typeof({attachments})='array' THEN to_jsonb(ARRAY(SELECT "
        f"jsonb_build_array(a->'filename', a->'mime', a->'size') FROM jsonb_array_elements({attachments})"
        f" WITH ORDINALITY AS parts(a, n) ORDER BY n)) ELSE {attachments} END")
    return f"jsonb_build_array({', '.join(fields)})"


GMAIL_CHANGED = f"{_gmail_material_sql('raw_items.payload')} IS DISTINCT FROM {_gmail_material_sql('EXCLUDED.payload')}"
UPDATE_GMAIL_METADATA = (
    "UPDATE raw_items SET payload=%s WHERE source=%s AND mailbox=%s AND source_id=%s"
    " AND payload IS DISTINCT FROM %s"
)


def _strip_nuls(value):
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {_strip_nuls(key): _strip_nuls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_nuls(item) for item in value]
    return value


def upsert_raw(conn, source: str, mailbox: str, source_id: str, payload: dict) -> int | None:
    return next(iter(upsert_raw_many(conn, source, mailbox, [(source_id, payload)])), None)


def upsert_raw_many(conn, source: str, mailbox: str, items: list[tuple[str, dict]]) -> set[int]:
    if not items:
        return set()
    if source == "gmail" and len({sid for sid, _ in items}) != len(items):
        return {raw_id for item in items for raw_id in upsert_raw_many(conn, source, mailbox, [item])}
    changes = GMAIL_CHANGED if source == "gmail" else "raw_items.payload IS DISTINCT FROM EXCLUDED.payload"
    args = [(source, mailbox, sid, Jsonb(_strip_nuls(p))) for sid, p in items]
    # The conflict check locks existing rows even when the change only updates metadata.
    with conn.cursor() as cur:
        cur.executemany(f"{UPSERT_RAW} WHERE {changes} RETURNING id", args, returning=True)
        results = [result.fetchone() for result in cur.results()]
        if source == "gmail":
            cur.executemany(UPDATE_GMAIL_METADATA, [
                (p, src, mb, sid, p) for (src, mb, sid, p), row in zip(args, results) if row is None
            ])
    return {row["id"] for row in results if row is not None}


def interrupt_stale(conn) -> None:
    conn.execute(
        "UPDATE jobs SET status='interrupted', control=NULL, finished_at=clock_timestamp()"
        " WHERE status IN ('running','paused') AND heartbeat_at < clock_timestamp()-interval '2 minutes'")


def reset(confirmed: bool = False) -> bool:
    tables = "job_log, jobs, signals, candidates, clients, raw_items, sync_state, gone_items"
    with connect() as conn:
        print(f"Host: {conn.info.host}  Database: {conn.info.dbname}")
        if not confirmed:
            for table in tables.split(", "):
                count = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
                print(f"{table}: {count}")
            print("Nothing changed. Run reset --yes to permanently clear these tables, including clients.")
            return False
        conn.execute("LOCK TABLE jobs IN EXCLUSIVE MODE")
        interrupt_stale(conn)
        if conn.execute("SELECT 1 FROM jobs WHERE status IN ('running','paused')").fetchone():
            print("Refusing reset: a job is still running.")
            return False
        conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    print("Reset complete; schema_migrations kept.")
    return True


if __name__ == "__main__":
    if sys.argv[1:] == ["push"]:
        applied = migrate()
        print("applied: " + ", ".join(applied) if applied else "up to date")
    elif sys.argv[1:] in (["reset"], ["reset", "--yes"]):
        sys.exit(0 if reset(confirmed="--yes" in sys.argv[1:]) else 1)
    else:
        sys.exit("usage: python -m scraper.db push | reset [--yes]")
