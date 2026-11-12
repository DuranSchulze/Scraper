"""Synthetic accounting workbooks and disposable Postgres only."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import Workbook
import pytest

from scraper import app as api
from scraper import auth, db, master
from test_auth import authenticated_client

GUI = {"X-Local-GUI": "1"}
HEADERS = ["REGISTERED NAME", "EMAIL ADDRESS", "Alias", "Trade Name", "TIN Number", "Intercompany",
           "Engagement Status", "Contact Person / Designation", "Email (for SI)", "Bank Account"]


def workbook(rows, headers=HEADERS, title="All Clients  Database", preamble=0, extra_sheet=None):
    book = Workbook()
    sheet = book.active
    sheet.title = title
    for _ in range(preamble):
        sheet.append(["Accounting master list"])
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    if extra_sheet:
        book.create_sheet(extra_sheet)
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


@pytest.fixture
def conn(pg_url, monkeypatch):
    monkeypatch.setenv("INTERNAL_DOMAINS", "acme.test")
    db.migrate()
    with db.connect() as connection:
        yield connection


def snapshot(conn):
    return {table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in ("master_clients", "master_imports", "candidates")}


def test_parser_aliases_addresses_flags_and_privacy():
    data = workbook([
        [" Alpha ", "Boss@CO.TEST; boss@co.test\nother@co.test, third@co.test", "A", "Trade", "001-2", "Yes", "Active", "Accountant", "si@co.test", "private-bank-123"],
        ["Named without email", "N/A"], ["-", "-"], [None, "only@other.test"],
        ["Bad email", "bad@@example.test invalid"],
    ], preamble=2)
    sheet, records, skipped, flagged = master.parse(data)
    assert sheet == "All Clients  Database" and len(records) == 4
    assert records[0] == {"row_no": 4, "registered_name": "Alpha", "alias": "A", "trade_name": "Trade", "tin": "001-2",
                          "intercompany": "Yes", "engagement_status": "Active", "contact_person": "Accountant",
                          "emails": ["boss@co.test", "other@co.test", "si@co.test", "third@co.test"], "domains": ["co.test"]}
    assert skipped == [{"row": 6, "reason": "No registered name or valid email"}]
    assert {item["row"] for item in flagged} == {5, 8}
    assert "private-bank-123" not in json.dumps((records, skipped, flagged))
    for headers in (["Company Name", "Email Addresses", "Contact Person"], ["Registered Name", "Email (for SI)", "Contact Person"]):
        assert master.parse(workbook([["Alias Co", "a@co.test", "Name"]], headers=headers))[1][0]["contact_person"] == "Name"


@pytest.mark.parametrize("kwargs,detail", [
    ({"title": "Unrelated"}, "Unrelated"),
    ({"extra_sheet": "allclientsdatabase"}, "allclientsdatabase"),
    ({"preamble": 10}, "headers"),
    ({"headers": ["Registered Name"] + ["Ignored"] * 99 + ["Email Address"]}, "headers"),
])
def test_sheet_header_and_width_rules(kwargs, detail):
    with pytest.raises(HTTPException) as error:
        master.parse(workbook([["Client", "a@co.test"]], **kwargs))
    assert error.value.status_code == 400 and detail in error.value.detail
    assert master.parse(workbook([["Client", "a@co.test"]], preamble=9))[1][0]["row_no"] == 11


def test_row_limit_and_forged_dimensions():
    data = workbook([["Client", "a@co.test"]] * 20_000)
    assert len(master.parse(data)[1]) == 20_000
    with pytest.raises(HTTPException, match="20,000") as error:
        master.parse(workbook([["Client", "a@co.test"]] * 20_001))
    assert error.value.status_code == 400
    source, output = ZipFile(BytesIO(workbook([["Client", "a@co.test"]]))), BytesIO()
    with source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                content = content.replace(b'dimension ref="A1:J2"', b'dimension ref="A1:A1"')
            target.writestr(name, content)
    assert master.parse(output.getvalue())[1][0]["emails"] == ["a@co.test"]


@pytest.mark.parametrize("data", [b"", b"not an xlsx", b"PK\x03\x04broken"])
def test_invalid_workbook(data):
    with pytest.raises(HTTPException) as error:
        master.parse(data)
    assert error.value.status_code == 400 and error.value.detail == "Not a valid .xlsx file"


def test_compressed_and_expanded_limits():
    with pytest.raises(HTTPException) as error:
        master.parse(b"x" * (master.MAX_BYTES + 1))
    assert error.value.status_code == 413
    data = BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as archive:
        archive.writestr("large.xml", b"x" * (master.MAX_EXPANDED + 1))
    assert len(data.getvalue()) < master.MAX_BYTES
    with pytest.raises(HTTPException) as error:
        master.parse(data.getvalue())
    assert error.value.status_code == 413


def seed_matches(conn):
    for key in ("boss@company.test", "new@company.test", "stranger@gmail.com", "person@gmail.com",
                "outsider@acme.test", "staff@acme.test", "x@unknown.test", "reviewed@company.test"):
        conn.execute("INSERT INTO candidates(email_key) VALUES (%s)", (key,))
    conn.execute("UPDATE candidates SET review_state='approved',master_match='email',master_ids=ARRAY[999] WHERE email_key='reviewed@company.test'")
    conn.commit()
    return workbook([
        ["Alpha", "boss@company.test person@gmail.com", "Alias", "Trade", "001-TIN"],
        ["Alpha Branch", "boss@company.test other@company.test"],
        ["Internal", "staff@acme.test"], ["Beta", "owner@second.test"],
    ])


def test_import_matches_and_directory_contract(conn, authenticated_client):
    data = seed_matches(conn)
    response = authenticated_client.post("/api/master/import?filename=synthetic.xlsx", content=data, headers=GUI)
    assert response.status_code == 200, response.text
    assert response.json() == {"rows": 4, "emails": 5, "skipped": [], "flagged": [],
                               "matched": {"email": 3, "domain": 1}, "dry_run": False}
    tags = {row["email_key"]: row for row in conn.execute("SELECT email_key,master_match,master_ids FROM candidates")}
    assert tags["boss@company.test"]["master_match"] == "email" and len(tags["boss@company.test"]["master_ids"]) == 2
    assert tags["new@company.test"]["master_match"] == "domain" and len(tags["new@company.test"]["master_ids"]) == 2
    for key in ("stranger@gmail.com", "outsider@acme.test", "x@unknown.test", "reviewed@company.test"):
        assert tags[key]["master_match"] is None and tags[key]["master_ids"] is None
    assert tags["person@gmail.com"]["master_match"] == tags["staff@acme.test"]["master_match"] == "email"
    summary = authenticated_client.get("/api/master").json()
    assert summary["tagged"] == {"email": 3, "domain": 1, "none": 3}
    assert summary["last_import"]["filename"] == "synthetic.xlsx"
    assert summary["last_import"]["imported_by"] == "admin@example.test"
    assert set(summary["last_import"]) == {"filename", "sheet", "rows", "emails", "imported_by", "imported_at"}
    rows = authenticated_client.get("/api/master/rows")
    assert rows.status_code == 200 and rows.headers["X-Total-Count"] == "4"
    assert [row["registered_name"] for row in rows.json()] == ["Alpha", "Alpha Branch", "Beta", "Internal"]
    assert [row["matched"] for row in rows.json()] == [3, 2, 0, 1]
    assert set(rows.json()[0]) == {"id", "row_no", *master.FIELDS, "emails", "matched"}
    assert authenticated_client.get("/api/master/rows?size=1&page=2").json() == [rows.json()[1]]
    assert authenticated_client.get("/api/master/rows?matched=no").json() == [rows.json()[2]]
    assert len(authenticated_client.get("/api/master/rows?matched=yes").json()) == 3
    for q in ("Alias", "Trade", "001-tin", "PERSON@GMAIL.COM"):
        assert authenticated_client.get("/api/master/rows", params={"q": q}).json() == [rows.json()[0]]
    for params in ({"size": 0}, {"size": 201}, {"page": 0}, {"matched": "invalid"}):
        assert authenticated_client.get("/api/master/rows", params=params).status_code == 422


def test_empty_directory(conn, authenticated_client):
    assert authenticated_client.get("/api/master").json() == {"last_import": None, "tagged": {"email": 0, "domain": 0, "none": 0}}
    response = authenticated_client.get("/api/master/rows")
    assert response.json() == [] and response.headers["X-Total-Count"] == "0"


def test_master_list_only_role_and_same_session_revocation(conn, authenticated_client):
    with db.connect() as connection:
        rid = connection.execute("INSERT INTO roles(name,routes) VALUES ('Accounting',ARRAY['masterlist']) RETURNING id").fetchone()["id"]
        user = connection.execute("INSERT INTO users(email,name,password_hash,role_id) VALUES ('accounting@test','Accounting','unused',%s) RETURNING id", (rid,)).fetchone()
    client = TestClient(api.app, base_url="http://localhost", cookies={auth.COOKIE: auth.sign_token(user)})
    response = client.post("/api/master/import", headers=GUI, content=workbook([["Client", "a@co.test"]]))
    assert response.status_code == 200
    assert client.get("/api/master").json()["last_import"]["imported_by"] == "accounting@test"
    assert len(client.get("/api/master/rows").json()) == 1
    for path in ("/api/roles", "/api/candidates", "/api/jobs/latest/mailboxes"):
        assert client.get(path).status_code == 403
    with db.connect() as connection:
        connection.execute("UPDATE roles SET routes=ARRAY['review'] WHERE id=%s", (rid,))
    assert client.get("/api/master").status_code == client.get("/api/master/rows").status_code == 403
    assert client.post("/api/master/import", headers=GUI, content=b"bad").status_code == 403
    client.close()


def test_replacement_dry_run_and_failure_are_atomic(conn, monkeypatch):
    master.import_workbook(seed_matches(conn), "old.xlsx", "test")
    before = snapshot(conn)
    replacement = workbook([["Replacement", "x@unknown.test"]])
    result = master.import_workbook(replacement, "new.xlsx", "test", check=True)
    assert result["dry_run"] is True and result["matched"] == {"email": 1, "domain": 0}
    assert snapshot(conn) == before
    original = master.rematch

    def fail(connection):
        original(connection)
        raise RuntimeError("injected rematch failure")

    with monkeypatch.context() as patch:
        patch.setattr(master, "rematch", fail)
        with pytest.raises(RuntimeError, match="injected"):
            master.import_workbook(replacement, "new.xlsx", "test")
    assert snapshot(conn) == before
    with pytest.raises(HTTPException):
        master.import_workbook(workbook([["Bad", "a@co.test"]], title="Unrelated"), "bad.xlsx", "test")
    assert snapshot(conn) == before
    master.import_workbook(replacement, "new.xlsx", "test")
    tags = {row["email_key"]: row for row in conn.execute("SELECT * FROM candidates")}
    assert tags["boss@company.test"]["master_ids"] is None
    assert tags["x@unknown.test"]["master_match"] == "email"
    assert conn.execute("SELECT count(*) AS n FROM master_imports").fetchone()["n"] == 2


def test_concurrent_imports_do_not_mix_workbooks(conn, monkeypatch):
    barrier = threading.Barrier(2)
    parse = master.parse

    def ready(data):
        result = parse(data)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(master, "parse", ready)

    def upload(prefix):
        return master.import_workbook(workbook([[prefix + "1", prefix + "1@co.test"], [prefix + "2", prefix + "2@co.test"]]), prefix + ".xlsx", "test")

    with ThreadPoolExecutor(2) as pool:
        assert all(result["rows"] == 2 for result in pool.map(upload, ["a", "b"]))
    latest = conn.execute("SELECT filename FROM master_imports ORDER BY id DESC LIMIT 1").fetchone()["filename"][0]
    assert {row["registered_name"] for row in conn.execute("SELECT * FROM master_clients")} == {latest + "1", latest + "2"}
    assert conn.execute("SELECT count(*) AS n FROM master_imports").fetchone()["n"] == 2


@pytest.mark.parametrize("content_length", [None, "1"])
def test_http_stream_limit_ignores_content_length(conn, authenticated_client, monkeypatch, content_length):
    monkeypatch.setattr(master, "parse", lambda _: pytest.fail("Oversize body reached parser"))
    headers = {**GUI, **({"Content-Length": content_length} if content_length else {})}
    response = authenticated_client.post("/api/master/import", headers=headers,
                                         content=(b"x" * (1024 * 1024) for _ in range(11)))
    assert response.status_code == 413
    assert conn.execute("SELECT count(*) AS n FROM master_imports").fetchone()["n"] == 0


def test_master_permission_and_write_guards(conn, authenticated_client):
    body = workbook([["Client", "a@co.test"]])
    client = authenticated_client
    assert client.post("/api/master/import", content=body).status_code == 403
    with db.connect() as connection:
        user = connection.execute("INSERT INTO users(email,name,password_hash,role_id) SELECT 'dev@test','Dev','unused',id FROM roles WHERE name='Developer' RETURNING id").fetchone()
    restricted = TestClient(api.app, base_url="http://localhost", cookies={auth.COOKIE: auth.sign_token(user)})
    for path in ("/api/master", "/api/master/rows"):
        assert restricted.get(path).status_code == 403
    assert restricted.post("/api/master/import", content=body, headers=GUI).status_code == 403
    restricted.cookies.clear()
    assert restricted.post("/api/master/import", content=body, headers=GUI).status_code == 401
    restricted.close()
    assert snapshot(conn)["master_clients"] == snapshot(conn)["master_imports"] == []
    response = client.post("/api/master/import?check=1", content=body, headers=GUI)
    assert response.status_code == 200 and response.json()["dry_run"] is True
    assert snapshot(conn)["master_clients"] == snapshot(conn)["master_imports"] == []
    assert client.post("/api/master/import", content=b"bad", headers=GUI).json() == {"detail": "Not a valid .xlsx file"}


def test_cli_dry_run_and_commit(conn, tmp_path):
    path = tmp_path / "synthetic.xlsx"
    path.write_bytes(workbook([["Client", "a@co.test"]]))
    command = [sys.executable, "-m", "scraper.master", "import", str(path)]
    dry = subprocess.run([*command, "--check"], cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(), capture_output=True, text=True, timeout=30)
    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["dry_run"] is True
    assert conn.execute("SELECT count(*) AS n FROM master_clients").fetchone()["n"] == 0
    saved = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert saved.returncode == 0, saved.stderr
    assert json.loads(saved.stdout)["rows"] == 1
    assert conn.execute("SELECT imported_by FROM master_imports").fetchone()["imported_by"] == "cli"
