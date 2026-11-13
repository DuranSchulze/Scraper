"""Disposable local Postgres; no test ever needs the configured Neon URL."""

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest


@pytest.fixture
def directory(conn):
    conn.execute("CREATE TABLE companies(id bigserial PRIMARY KEY,name text NOT NULL)")
    conn.execute("CREATE TABLE contacts(id bigserial PRIMARY KEY,company_id bigint REFERENCES companies(id),emails text[])")
    conn.commit()

    def replace(rows):
        conn.execute("DELETE FROM contacts")
        conn.execute("DELETE FROM companies")
        ids = []
        for name, emails in rows:
            cid = conn.execute("INSERT INTO companies(name) VALUES (%s) RETURNING id", (name,)).fetchone()["id"]
            conn.execute("INSERT INTO contacts(company_id,emails) VALUES (%s,%s)", (cid, emails))
            ids.append(cid)
        conn.commit()
        return ids

    return replace


@pytest.fixture(scope="session")
def pg_server():
    default_bin = (Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PostgreSQL/17/bin"
                   if sys.platform == "win32" else
                   Path("/opt/homebrew/opt/postgresql@17/bin" if sys.platform == "darwin"
                        else "/usr/lib/postgresql/17/bin"))
    bin_dir = Path(os.environ.get("PG_BIN", str(default_bin)))
    suffix = ".exe" if sys.platform == "win32" else ""
    if "PG_BIN" not in os.environ and not all(
            (bin_dir / (name + suffix)).is_file() for name in ("initdb", "pg_ctl")) and sys.platform == "win32":
        versions = sorted((path for path in default_bin.parent.parent.iterdir() if path.name.isdigit()),
                          key=lambda path: int(path.name), reverse=True) if default_bin.parent.parent.is_dir() else []
        bin_dir = next((path / "bin" for path in versions if all(
            (path / "bin" / (name + suffix)).is_file() for name in ("initdb", "pg_ctl"))), bin_dir)
    if not all((bin_dir / (name + suffix)).is_file() for name in ("initdb", "pg_ctl")):
        pytest.fail("Postgres binaries missing: install PostgreSQL 17 (Windows: choco install postgresql17; "
                    "macOS: brew install postgresql@17; Linux: install postgresql-17) "
                    "and set PG_BIN to its bin folder if needed")
    root = Path(tempfile.mkdtemp(prefix="scraper-pg-"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    options = f"-h 127.0.0.1 -p {port}"
    if sys.platform != "win32":
        options += " -c unix_socket_directories=''"
    started = False
    try:
        subprocess.run([str(bin_dir / ("initdb" + suffix)), "-D", str(root / "data"), "-A", "trust",
                        "-U", "postgres", "--locale=C", "--encoding=UTF8"],
                       check=True, capture_output=True, text=True)
        # Windows server children inherit pipes, so capturing pg_ctl output can hang.
        with (root / "control.log").open("w") as output:
            subprocess.run([str(bin_dir / ("pg_ctl" + suffix)), "-D", str(root / "data"), "-l", str(root / "server.log"),
                            "-o", options, "-w", "start"],
                           check=True, stdout=output, stderr=subprocess.STDOUT)
        started = True
        yield make_conninfo(host="127.0.0.1", port=port, user="postgres", dbname="postgres")
    finally:
        if started:
            subprocess.run([str(bin_dir / ("pg_ctl" + suffix)), "-D", str(root / "data"), "-m", "immediate", "-w", "stop"],
                           check=True, capture_output=True, text=True)
        shutil.rmtree(root)


@pytest.fixture
def pg_url(pg_server, monkeypatch):
    assert conninfo_to_dict(pg_server)["host"] == "127.0.0.1"
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
