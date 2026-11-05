"""Neon Postgres access. Run `uv run python -m scraper.db migrate` to apply migrations."""

import sys

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import ROOT, env

MIGRATIONS_DIR = ROOT / "migrations"


def connect() -> psycopg.Connection:
    url = env("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    return psycopg.connect(url, row_factory=dict_row)


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


def missing_ids(conn, source: str, mailbox: str, ids: list[str], min_version: int = 1) -> list[str]:
    """Ids not yet stored (or stored with an older payload version), in input order."""
    have = {
        r["source_id"]
        for r in conn.execute(
            "SELECT source_id FROM raw_items WHERE source = %s AND mailbox = %s AND source_id = ANY(%s)"
            " AND coalesce((payload->>'v')::int, 1) >= %s",
            (source, mailbox, ids, min_version),
        )
    }
    return [i for i in ids if i not in have]


def outdated_gmail_ids(conn, mailbox: str, min_version: int) -> list[str]:
    return [r["source_id"] for r in conn.execute(
        "SELECT source_id FROM raw_items WHERE source = 'gmail' AND mailbox = %s"
        " AND coalesce((payload->>'v')::int, 1) < %s ORDER BY id",
        (mailbox, min_version),
    )]


UPSERT_RAW = (
    "INSERT INTO raw_items (source, mailbox, source_id, payload) VALUES (%s, %s, %s, %s)"
    " ON CONFLICT (source, mailbox, source_id)"
    # A changed payload must be extracted again.
    " DO UPDATE SET payload = EXCLUDED.payload, fetched_at = now(), extracted_at = NULL"
)


def upsert_raw(conn, source: str, mailbox: str, source_id: str, payload: dict) -> None:
    conn.execute(UPSERT_RAW, (source, mailbox, source_id, Jsonb(payload)))


def upsert_raw_many(conn, source: str, mailbox: str, items: list[tuple[str, dict]]) -> None:
    with conn.cursor() as cur:
        cur.executemany(UPSERT_RAW, [(source, mailbox, sid, Jsonb(p)) for sid, p in items])


if __name__ == "__main__":
    if sys.argv[1:] != ["migrate"]:
        sys.exit("usage: python -m scraper.db migrate")
    applied = migrate()
    print("applied: " + ", ".join(applied) if applied else "up to date")
