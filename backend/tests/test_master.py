"""Directory matching against synthetic tables in disposable Postgres only."""

import logging
from datetime import datetime

from fastapi.testclient import TestClient
import pytest

from scraper import app as api
from scraper import auth, db, master
from test_auth import authenticated_client


@pytest.fixture
def conn(pg_url, monkeypatch):
    monkeypatch.setenv("INTERNAL_DOMAINS", "acme.test")
    db.migrate()
    with db.connect() as connection:
        yield connection


def candidates(conn, emails):
    for email in emails:
        conn.execute("INSERT INTO candidates(email_key) VALUES (%s)", (email,))
    conn.commit()


def tags(conn):
    return {r["email_key"]: (r["master_match"], r["master_ids"])
            for r in conn.execute("SELECT email_key,master_match,master_ids FROM candidates")}


def test_directory_email_domain_precedence_and_pending_only(conn, directory):
    first, second, internal = directory([
        ("Alpha", ["Boss@Company.Test", "owner@gmail.com"]),
        ("Branch", ["boss@company.test", "other@company.test"]),
        ("Internal", ["staff@acme.test"]),
    ])
    conn.execute("INSERT INTO contacts(company_id,emails) VALUES (%s,ARRAY['boss@company.test','boss@company.test'])", (first,))
    conn.execute("INSERT INTO contacts(company_id,emails) VALUES (%s,NULL),(%s,ARRAY[NULL,''])", (first, first))
    conn.commit()
    candidates(conn, ["boss@company.test", "new@company.test", "owner@gmail.com", "stranger@gmail.com",
                      "staff@acme.test", "outsider@acme.test", "unknown@none.test", "reviewed@company.test"])
    conn.execute("UPDATE candidates SET review_state='approved',master_match='email',master_ids=ARRAY[999] WHERE email_key='reviewed@company.test'")
    before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in ("companies", "contacts")}
    master.rematch(conn)
    assert tags(conn) == {
        "boss@company.test": ("email", [first, second]), "new@company.test": ("domain", [first, second]),
        "owner@gmail.com": ("email", [first]), "stranger@gmail.com": (None, None),
        "staff@acme.test": ("email", [internal]), "outsider@acme.test": (None, None),
        "unknown@none.test": (None, None), "reviewed@company.test": (None, None),
    }
    assert {t: conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in before} == before
    assert master.summary(conn) == {"available": True, "companies": 3, "contacts": 6,
                                    "email": 3, "domain": 1, "none": 3,
                                    "tagged_at": master.summary(conn)["tagged_at"]}
    assert master.summary(conn)["tagged_at"].utcoffset().total_seconds() == 0


def test_regional_free_mail_never_domain_matches(conn, directory):
    domains = ["yahoo.com.ph", "yahoo.co.uk", "yahoo.de", "ymail.ph", "rocketmail.ph", "hotmail.co.uk",
               "outlook.ph", "live.com.ph", "msn.ph", "gmail.ph", "googlemail.ph", "aol.ph", "icloud.ph",
               "me.ph", "mac.ph", "proton.ph", "protonmail.ph", "gmx.ph", "mail.ph", "zoho.ph", "yandex.ph",
               "company.test", "yahoocorp.test"]
    directory([(domain, [f"owner@{domain}"]) for domain in domains])
    candidates(conn, [f"{local}@{domain}" for domain in domains for local in ("owner", "stranger")])
    master.rematch(conn)
    timestamp = master.summary(conn)["tagged_at"]
    conn.commit()

    def assert_tags():
        result = tags(conn)
        for domain in domains:
            assert result[f"owner@{domain}"][0] == "email"
            stranger = result[f"stranger@{domain}"]
            if domain in ("company.test", "yahoocorp.test"):
                assert stranger[0] == "domain" and len(stranger[1]) == 1
            else:
                assert stranger == (None, None)

    assert_tags()
    keys = [f"stranger@{domain}" for domain in domains]
    conn.execute("UPDATE candidates SET master_match='domain',master_ids=ARRAY[999] WHERE email_key=ANY(%s)", (keys,))
    master.rematch(conn, keys)
    assert_tags()
    assert master.summary(conn)["tagged_at"] == timestamp


def test_incremental_scope_and_full_retag_rollback(conn, directory):
    first, = directory([("Alpha", ["owner@company.test"])])
    candidates(conn, ["owner@company.test", "new@company.test"])
    master.rematch(conn, ["owner@company.test"])
    conn.commit()
    assert tags(conn) == {"owner@company.test": ("email", [first]), "new@company.test": (None, None)}
    assert master.summary(conn)["tagged_at"] is None
    master.rematch(conn)
    assert master.summary(conn)["tagged_at"] is not None
    with db.connect() as observer:
        assert master.summary(observer)["tagged_at"] is None
    conn.rollback()
    assert tags(conn)["new@company.test"] == (None, None)
    assert master.summary(conn)["tagged_at"] is None
    master.rematch(conn)
    conn.commit()
    committed = master.summary(conn)["tagged_at"]
    master.rematch(conn)
    assert master.summary(conn)["tagged_at"] > committed
    conn.rollback()
    assert master.summary(conn)["tagged_at"] == committed


@pytest.mark.parametrize("present", [None, "contacts", "companies"])
def test_absent_directory_clears_all_tags_logs_once_and_serves_empty_names(conn, authenticated_client, monkeypatch, caplog, present):
    if present == "contacts":
        conn.execute("CREATE TABLE contacts(company_id bigint,emails text[])")
    elif present == "companies":
        conn.execute("CREATE TABLE companies(id bigint,name text)")
    candidates(conn, ["a@company.test", "b@company.test"])
    conn.execute("UPDATE candidates SET master_match='domain',master_ids=ARRAY[999]")
    conn.commit()
    assert all(r["master_names"] == [] for r in authenticated_client.get("/api/candidates").json())
    monkeypatch.setattr(master, "_missing_logged", False)
    with caplog.at_level(logging.WARNING, logger="scraper.master"):
        master.rematch(conn, ["a@company.test"])
        master.rematch(conn, ["a@company.test"])
    assert all(tag == (None, None) for tag in tags(conn).values())
    assert sum("Client Directory tables unavailable" in r.message for r in caplog.records) == 1
    conn.commit()
    assert authenticated_client.get("/api/master/summary").json() == {
        "available": False, "companies": 0, "contacts": 0, "email": 0, "domain": 0, "none": 2, "tagged_at": None}


def test_summary_route_contract_permissions_and_removed_endpoints(conn, directory, authenticated_client):
    directory([("Company", ["owner@company.test"])])
    candidates(conn, ["owner@company.test"])
    master.rematch(conn)
    conn.commit()
    summary = authenticated_client.get("/api/master/summary")
    assert summary.status_code == 200
    assert summary.json() == {"available": True, "companies": 1, "contacts": 1,
                              "email": 1, "domain": 0, "none": 0, "tagged_at": summary.json()["tagged_at"]}
    assert datetime.fromisoformat(summary.json()["tagged_at"]).utcoffset().total_seconds() == 0
    for method, path in (("GET", "/api/master"), ("GET", "/api/master/rows"), ("POST", "/api/master/import")):
        assert authenticated_client.request(method, path, headers={"X-Local-GUI": "1"}).status_code == 404
    for routes in (["review"], ["accounts"], ["scraping"]):
        with db.connect() as connection:
            role = connection.execute("INSERT INTO roles(name,routes) VALUES (%s,%s) RETURNING id", (routes[0], routes)).fetchone()["id"]
            user = connection.execute("INSERT INTO users(email,name,password_hash,role_id) VALUES (%s,'Test','unused',%s) RETURNING id",
                                      (routes[0] + '@test', role)).fetchone()
        with TestClient(api.app, base_url="http://localhost", cookies={auth.COOKIE: auth.sign_token(user)}) as client:
            assert client.get("/api/master/summary").status_code == (200 if "review" in routes else 403)
