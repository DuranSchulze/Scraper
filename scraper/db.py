"""Neon Postgres access. Run `uv run python -m scraper.db migrate` to apply migrations."""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def connect() -> psycopg.Connection:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    return psycopg.connect(url, row_factory=dict_row)


def pending_migrations(applied: set[str]) -> list[Path]:
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


if __name__ == "__main__":
    if sys.argv[1:] != ["migrate"]:
        sys.exit("usage: python -m scraper.db migrate")
    applied = migrate()
    print("applied: " + ", ".join(applied) if applied else "up to date")
