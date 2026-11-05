"""Disposable socket-only Postgres; no test ever needs the configured Neon URL."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest


@pytest.fixture(scope="session")
def pg_server():
    bin_dir = Path(os.environ.get("PG_BIN", "/opt/homebrew/opt/postgresql@17/bin"))
    if not all((bin_dir / name).is_file() for name in ("initdb", "pg_ctl")):
        pytest.fail("Postgres binaries missing: brew install postgresql@17 (or set PG_BIN)")
    root = Path(tempfile.mkdtemp(prefix="scraper-pg-", dir="/tmp"))
    env = {**os.environ, "LC_ALL": "en_US.UTF-8"}
    started = False
    try:
        subprocess.run([str(bin_dir / "initdb"), "-D", str(root / "data"), "-A", "trust", "-U", "postgres"],
                       env=env, check=True, capture_output=True, text=True)
        subprocess.run([str(bin_dir / "pg_ctl"), "-D", str(root / "data"), "-l", str(root / "server.log"),
                        "-o", f"-k {root} -c listen_addresses=''", "-w", "start"],
                       env=env, check=True, capture_output=True, text=True)
        started = True
        yield make_conninfo(host=str(root), user="postgres", dbname="postgres")
    finally:
        if started:
            subprocess.run([str(bin_dir / "pg_ctl"), "-D", str(root / "data"), "-m", "immediate", "-w", "stop"],
                           env=env, check=True, capture_output=True, text=True)
        shutil.rmtree(root)


@pytest.fixture
def pg_url(pg_server, monkeypatch):
    name = "test_" + uuid.uuid4().hex
    with psycopg.connect(pg_server, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_conninfo(pg_server, dbname=name)
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        yield url
    finally:
        with psycopg.connect(pg_server, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
