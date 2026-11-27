"""Authentication uses fixture accounts and disposable local Postgres only."""

import json
import time
import runpy
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from fastapi.testclient import TestClient
import jwt
import pytest
from pydantic import ValidationError
import uvicorn

from scraper import auth, db
from scraper.app import app
from scraper.config import jwt_secret

GUI = {"X-Local-GUI": "1"}
SECRET = "test-only-secret-with-at-least-64-characters-for-hmac-algorithm-tests"
PASSWORD = "test-password-123"
EMAIL = "admin@example.test"
STRONG_PASSWORD = "Long!NewPass3"


def login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers=GUI)


def change_password(client, current, new):
    return client.post("/api/auth/password", json={"current_password": current, "new_password": new}, headers=GUI)


def legacy_token(user_id):
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + 100}, SECRET, algorithm="HS256")


def role_id(name):
    with db.connect() as conn:
        return conn.execute("SELECT id FROM roles WHERE name=%s", (name,)).fetchone()["id"]


def new_role(client, name="Limited", routes=("review",)):
    response = client.post("/api/roles", json={"name": name, "routes": list(routes)}, headers=GUI)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def authenticated_client(pg_url, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("ADMIN_EMAIL", "")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    monkeypatch.setenv("APP_URL", "")
    auth.buckets.clear()
    monkeypatch.setattr(auth, "last_sweep", 0)
    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO users(email,name,password_hash,role_id) SELECT %s,'Admin',%s,id FROM roles WHERE name='Owner'",
                     (EMAIL, auth.hash_password(PASSWORD)))
    client = TestClient(app, base_url="http://localhost")
    assert client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=GUI).status_code == 200
    auth.buckets.clear()
    try:
        yield client
    finally:
        client.close()
        auth.buckets.clear()


def test_login_cookie_me_and_logout(authenticated_client):
    client = authenticated_client
    response = client.post("/api/auth/login", json={"email": EMAIL.upper(), "password": PASSWORD}, headers=GUI)
    user = response.json()
    assert user == {"id": 1, "email": EMAIL, "name": "Admin", "role": {"id": role_id("Owner"), "name": "Owner"},
                    "routes": list(auth.ROUTES), "must_change_password": False, "session_version": 0}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Max-Age=28800" in cookie and "Secure" not in cookie
    claims = jwt.decode(client.cookies.get(auth.COOKIE), SECRET, algorithms=["HS256"])
    assert claims["sub"] == "1" and claims["ver"] == 0 and "role" not in claims and claims["exp"] - claims["iat"] == 28800
    assert client.get("/api/auth/me").json() == user
    response = client.post("/api/auth/logout", headers=GUI)
    assert response.json() == {"ok": True}
    assert all(value in response.headers["set-cookie"] for value in ("Max-Age=0", "HttpOnly", "SameSite=lax", "Path=/"))
    assert client.get("/api/auth/me").status_code == 401


def test_writes_require_matching_expected_user_when_supplied(authenticated_client):
    admin = authenticated_client
    admin_id = admin.get("/api/auth/me").json()["id"]
    email = "member@example.test"
    created = admin.post("/api/users", headers=GUI, json={
        "email": email, "name": "Member", "role_id": role_id("Developer"),
    })
    assert created.status_code == 201
    member_id = created.json()["id"]
    password = created.json()["temporary_password"]
    stale_headers = {**GUI, "X-Expected-User": str(admin_id)}
    schedule = "/api/schedule"
    body = {"enabled": True, "interval_minutes": 240}
    assert admin.put(schedule, headers=stale_headers, json={"enabled": False, "interval_minutes": 120}).status_code == 200

    member = TestClient(app, base_url="http://localhost")
    try:
        assert member.put(schedule, headers=stale_headers, json=body).status_code == 401
        login = member.post("/api/auth/login", headers=stale_headers, json={"email": email, "password": password})
        assert login.status_code == 200 and login.json()["id"] == member_id
        assert change_password(member, password, STRONG_PASSWORD).status_code == 200
        original = member.get(schedule, headers=stale_headers)
        assert original.status_code == 200

        for header in (str(admin_id), "abc", "", f"0{member_id}"):
            response = member.put(schedule, headers={**GUI, "X-Expected-User": header}, json=body)
            assert response.status_code == 409
            assert response.json() == {"detail": "The signed-in account changed in another tab. Nothing was saved; reload the page."}
        assert member.get(schedule).json() == original.json()

        saved = member.put(schedule, headers={**GUI, "X-Expected-User": str(member_id)}, json=body)
        assert saved.status_code == 200 and saved.json()["interval_minutes"] == 240
        assert member.put(schedule, headers=GUI, json={**body, "interval_minutes": 120}).status_code == 200
        assert member.get(schedule).json()["interval_minutes"] == 120

        logout = member.post("/api/auth/logout", headers=stale_headers)
        assert logout.status_code == 200 and "Max-Age=0" in logout.headers["set-cookie"]
        assert member.put(schedule, headers=stale_headers, json=body).status_code == 401
    finally:
        member.close()


@pytest.mark.parametrize("https,configured", [(True, False), (False, True)])
def test_secure_cookie_on_https_and_configured_https(authenticated_client, monkeypatch, https, configured):
    monkeypatch.setenv("APP_URL", "https://localhost" if configured else "")
    client = TestClient(app, base_url="https://localhost" if https else "http://localhost")
    response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=GUI)
    assert response.status_code == 200 and "Secure" in response.headers["set-cookie"]
    # A Secure cookie is sent only over HTTPS, even when the application URL forces it.
    client.base_url = "https://localhost"
    response = client.post("/api/auth/logout", headers=GUI)
    assert response.status_code == 200 and "Secure" in response.headers["set-cookie"]


def test_unknown_user_and_wrong_password_are_identical(authenticated_client):
    client = authenticated_client
    assert client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 403
    a = client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong"}, headers=GUI)
    b = client.post("/api/auth/login", json={"email": "unknown@example.test", "password": "wrong"}, headers=GUI)
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


@pytest.mark.parametrize("configured,expected", [(None, "lax"), ("lax", "lax"), ("none", "none"),
                                               ("strict", "strict"), ("invalid", "lax")])
def test_configured_cookie_samesite(authenticated_client, monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv("COOKIE_SAMESITE", raising=False)
    else:
        monkeypatch.setenv("COOKIE_SAMESITE", configured)
    response = authenticated_client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=GUI)
    assert response.status_code == 200
    if expected == "none":
        authenticated_client.base_url = "https://localhost"
    for cookie in (response.headers["set-cookie"],
                   authenticated_client.post("/api/auth/logout", headers=GUI).headers["set-cookie"]):
        assert f"SameSite={expected}" in cookie and "HttpOnly" in cookie and "Path=/" in cookie
        assert ("Secure" in cookie) == (expected == "none")


@pytest.mark.parametrize("kind", ["expired", "tampered", "wrong_secret", "missing_claim", "wrong_algorithm", "bad_subject"])
def test_invalid_tokens_are_unauthorized(authenticated_client, kind):
    now = int(time.time())
    claims = {"sub": "1", "role": "admin", "iat": now, "exp": now + 100}
    if kind == "expired":
        claims["exp"] = now - 1
    if kind == "missing_claim":
        del claims["iat"]
    if kind == "bad_subject":
        claims["sub"] = "9" * 30
    token = jwt.encode(claims, "wrong-secret-with-at-least-32-characters" if kind == "wrong_secret" else SECRET,
                       algorithm="HS384" if kind == "wrong_algorithm" else "HS256")
    if kind == "tampered":
        header, payload, signature = token.split(".")
        token = ".".join((header, payload, ("a" if signature[0] != "a" else "b") + signature[1:]))
    authenticated_client.cookies.set(auth.COOKIE, token, domain="localhost.local", path="/")
    assert authenticated_client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("change", ["inactive", "deleted", "demoted"])
def test_user_is_reread_and_role_is_from_database(authenticated_client, change):
    with db.connect() as conn:
        conn.execute({"inactive": "UPDATE users SET active=false", "deleted": "DELETE FROM users",
                      "demoted": "UPDATE users SET role_id=(SELECT id FROM roles WHERE name='Developer')"}[change])
    response = authenticated_client.get("/api/auth/me")
    if change == "demoted":
        assert response.status_code == 200 and response.json()["role"]["name"] == "Developer"
        assert authenticated_client.get("/api/users").status_code == 403
        assert authenticated_client.post("/api/users", json={}, headers=GUI).status_code == 403
    else:
        assert response.status_code == 401


def registered_routes(routes):
    for route in routes:
        if hasattr(route, "original_router"):
            yield from registered_routes(route.original_router.routes)
        else:
            yield route


API_ROUTES = [(route.path, method) for route in registered_routes(app.routes) if route.path.startswith("/api/")
              for method in route.methods if (route.path, method) != ("/api/auth/login", "POST")]


@pytest.mark.parametrize("path,method", API_ROUTES)
def test_every_api_route_requires_auth_before_validation(path, method):
    path = path.replace("{job_id}", "1").replace("{cid}", "1").replace("{item_id}", "1")
    client = TestClient(app, base_url="http://localhost")
    assert client.request(method, path, headers=GUI).status_code == 401
    if method != "GET":
        assert client.request(method, path).status_code == 403


def test_users_create_list_and_case_insensitive_duplicates(authenticated_client, caplog):
    caplog.set_level("DEBUG")
    body = {"email": "Member@Example.test", "name": " Member ", "role_id": role_id("Developer")}
    response = authenticated_client.post("/api/users", json=body, headers=GUI)
    assert response.status_code == 201
    created = response.json()
    assert created["email"] == "member@example.test" and created["name"] == "Member" and created["active"] is True
    assert set(created) == {"id", "email", "name", "role", "routes", "active", "created_at",
                            "must_change_password", "session_version", "temporary_password"}
    assert created["must_change_password"] is True and created["session_version"] == 0
    password = created["temporary_password"]
    assert len(password) == 16 and auth.validate_password(password, created["email"]) is None
    assert response.headers["Cache-Control"] == "no-store"
    assert created["routes"] == ["review", "scraping"]
    users = authenticated_client.get("/api/users").json()
    assert len(users) == 2 and all(not {"password_hash", "temporary_password"} & user.keys() for user in users)
    assert users[-1]["must_change_password"] is True
    assert authenticated_client.post("/api/users", json=body, headers=GUI).status_code == 409
    with db.connect() as conn:
        stored = conn.execute("SELECT * FROM users WHERE id=%s", (created["id"],)).fetchone()
    assert password not in stored.values() and auth.verify_password(password, stored["password_hash"])
    assert password not in caplog.text


@pytest.mark.parametrize("routes", [
    ["review"], ["scraping"], ["scraping", "review", "scraping"], ["accounts", "review"],
])
def test_stored_and_effective_routes_are_canonical(authenticated_client, routes):
    admin = authenticated_client
    role = new_role(admin, routes=routes)
    expected = [key for key in auth.ROUTES if key in routes]
    assert role["routes"] == expected
    created = admin.post("/api/users", headers=GUI, json={
        "email": "routes@example.test", "name": "Routes", "role_id": role["id"],
    })
    assert created.status_code == 201 and created.json()["routes"] == expected
    # Responses canonicalize arrays even when an external writer orders them differently.
    with db.connect() as conn:
        conn.execute("UPDATE roles SET routes=%s WHERE id=%s", (routes, role["id"]))
    assert admin.get("/api/users").json()[-1]["routes"] == expected
    with TestClient(app, base_url="http://localhost") as member:
        response = login(member, "routes@example.test", created.json()["temporary_password"])
        assert response.status_code == 200
        assert set(response.json()) == {"id", "email", "name", "role", "routes", "must_change_password", "session_version"}
        assert response.json()["routes"] == expected
        assert member.get("/api/auth/me").json() == response.json()
        claims = jwt.decode(member.cookies.get(auth.COOKIE), SECRET, algorithms=["HS256"])
        assert set(claims) == {"sub", "iat", "exp", "ver"}


@pytest.mark.parametrize("routes", [[], ["unknown"], ["masterlist"], ["review", "unknown"], [None], None, "review"])
def test_create_and_patch_reject_invalid_routes(authenticated_client, routes):
    admin = authenticated_client
    assert admin.post("/api/roles", headers=GUI, json={"name": "Invalid", "routes": routes}).status_code == 422
    role = new_role(admin)
    assert admin.patch(f"/api/roles/{role['id']}", headers=GUI,
                       json={"name": "Invalid", "routes": routes}).status_code == 422
    assert admin.get("/api/roles").json()[-1] == role


@pytest.mark.parametrize("revoked,endpoint", [("review", "/api/candidates"),
                                               ("scraping", "/api/jobs/latest/mailboxes"),
                                               ("accounts", "/api/roles")])
def test_role_edit_revokes_same_session_immediately(authenticated_client, revoked, endpoint):
    admin = authenticated_client
    role = new_role(admin, routes=list(auth.ROUTES))
    created = admin.post("/api/users", headers=GUI, json={
        "email": "routes@example.test", "name": "Routes", "role_id": role["id"],
    }).json()
    with TestClient(app, base_url="http://localhost") as member:
        assert login(member, created["email"], created["temporary_password"]).status_code == 200
        assert change_password(member, created["temporary_password"], STRONG_PASSWORD).status_code == 200
        cookie = member.cookies.get(auth.COOKIE)
        assert member.get(endpoint).status_code == 200
        kept = [key for key in auth.ROUTES if key != revoked]
        response = admin.patch(f"/api/roles/{role['id']}", headers=GUI, json={"name": "Changed", "routes": kept})
        assert response.status_code == 200
        assert member.cookies.get(auth.COOKIE) == cookie
        assert member.get(endpoint).status_code == 403
        assert member.get("/api/auth/me").json()["routes"] == kept
        assert admin.get("/api/users").json()[-1]["routes"] == kept


@pytest.mark.parametrize("change", [{"email": "invalid"}, {"email": "a@b"}, {"email": "a@bad_domain.test"},
                                    {"email": "a@-bad.test"}, {"email": "a..b@example.test"}, {"name": " "},
                                    {"password": PASSWORD}, {"role_id": 0}])
def test_user_validation(authenticated_client, change):
    body = {"email": "new@example.test", "name": "New", "role_id": role_id("Developer"), **change}
    response = authenticated_client.post("/api/users", json=body, headers=GUI)
    assert response.status_code == 422 and isinstance(response.json()["detail"], list)
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 1


def test_legacy_cookie_role_claim_is_ignored(authenticated_client):
    client = authenticated_client
    now = int(time.time())
    token = jwt.encode({"sub": "1", "role": "member", "iat": now, "exp": now + 100}, SECRET, algorithm="HS256")
    client.cookies.set(auth.COOKIE, token, domain="localhost.local", path="/")
    assert client.get("/api/auth/me").json()["role"]["name"] == "Owner"
    assert client.get("/api/roles").status_code == 200


@pytest.mark.parametrize("version", [None, False, True, "0", 0.0, -1, 2147483648, 1])
def test_invalid_or_stale_session_versions_are_unauthorized(authenticated_client, version):
    claims = jwt.decode(legacy_token(1), SECRET, algorithms=["HS256"])
    token = jwt.encode({**claims, "ver": version}, SECRET, algorithm="HS256")
    authenticated_client.cookies.set(auth.COOKIE, token, domain="localhost.local", path="/")
    assert authenticated_client.get("/api/auth/me").status_code == 401


def test_migration_and_temporary_password_lifecycle(pg_url, monkeypatch, caplog):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("APP_URL", "")
    monkeypatch.setenv("COOKIE_SAMESITE", "lax")
    monkeypatch.setattr(auth, "buckets", {})
    caplog.set_level("DEBUG")
    migration = "017_temp_passwords.sql"
    with monkeypatch.context() as patch:
        pending = db.pending_migrations
        patch.setattr(db, "pending_migrations", lambda applied: [p for p in pending(applied) if p.name < migration])
        assert db.migrate()[-1] == "016_schedule_min_interval.sql"
    with db.connect() as conn:
        stored_hash = auth.hash_password(PASSWORD)
        conn.execute(
            "INSERT INTO users(email,name,password_hash,role_id) VALUES (%s,'Admin',%s,%s),(%s,'Member',%s,%s)",
            (EMAIL, stored_hash, role_id("Owner"), "member@example.test", stored_hash, role_id("Developer")))
        before = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    legacy = legacy_token(2)
    assert db.migrate() == [migration]
    assert db.migrate() == []
    with db.connect() as conn:
        assert conn.execute("SELECT * FROM users ORDER BY id").fetchall() == [
            {**row, "must_change_password": False, "session_version": 0} for row in before
        ]

    with (TestClient(app, base_url="http://localhost", cookies={auth.COOKIE: legacy_token(1)}) as admin,
          TestClient(app, base_url="http://localhost") as member):
        member.cookies.set(auth.COOKIE, legacy, domain="localhost.local", path="/")
        assert member.get("/api/auth/me").json()["must_change_password"] is False
        assert member.get("/api/jobs/latest/mailboxes").status_code == 200
        reset = admin.post("/api/users/2/reset-password", headers=GUI)
        assert reset.status_code == 200 and reset.headers["Cache-Control"] == "no-store"
        assert set(reset.json()) == {"temporary_password"}
        temporary = reset.json()["temporary_password"]
        assert len(temporary) == 16 and auth.validate_password(temporary, "member@example.test") is None
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=2").fetchone()
        assert row["session_version"] == 1 and row["must_change_password"] is True
        assert temporary not in row.values() and auth.verify_password(temporary, row["password_hash"])
        assert member.get("/api/auth/me").status_code == 401
        assert login(member, "member@example.test", PASSWORD).status_code == 401

        signed_in = login(member, "member@example.test", temporary)
        assert signed_in.status_code == 200 and signed_in.json()["must_change_password"] is True
        assert member.get("/api/auth/me").json() == signed_in.json()
        for path, method in API_ROUTES:
            if (method, path) in {("GET", "/api/auth/me"), ("POST", "/api/auth/logout"), ("POST", "/api/auth/password")}:
                continue
            blocked = member.request(method, path, headers=GUI)
            assert blocked.status_code == 403 and blocked.json() == {"detail": "Password change required"}, (method, path)
        assert member.post("/api/auth/logout", headers=GUI).status_code == 200
        assert member.get("/api/auth/me").status_code == 401
        assert login(member, "member@example.test", temporary).status_code == 200
        temporary_cookie = member.cookies.get(auth.COOKIE)

        changed = change_password(member, temporary, STRONG_PASSWORD)
        assert changed.status_code == 200 and "HttpOnly" in changed.headers["set-cookie"]
        assert changed.json()["must_change_password"] is False and changed.json()["session_version"] == 2
        assert member.cookies.get(auth.COOKIE) != temporary_cookie
        assert member.get("/api/auth/me").json() == changed.json()
        assert member.get("/api/jobs/latest/mailboxes").status_code == 200
        with TestClient(app, base_url="http://localhost", cookies={auth.COOKIE: temporary_cookie}) as stale:
            assert stale.get("/api/auth/me").status_code == 401
            assert login(stale, "member@example.test", temporary).status_code == 401
            assert login(stale, "member@example.test", STRONG_PASSWORD).status_code == 200
    assert temporary not in caplog.text and STRONG_PASSWORD not in caplog.text


@pytest.mark.parametrize("method,path", [("POST", "/api/users"), ("PUT", "/api/schedule")])
def test_forced_change_is_checked_before_json_parsing(authenticated_client, method, path):
    def malformed_request():
        return authenticated_client.request(method, path, headers={**GUI, "Content-Type": "application/json"},
                                            content="{")

    assert malformed_request().status_code == 422
    with db.connect() as conn:
        conn.execute("UPDATE users SET must_change_password=true")
    blocked = malformed_request()
    assert blocked.status_code == 403 and blocked.json() == {"detail": "Password change required"}


def test_reset_errors_and_create_share_the_rate_limit(authenticated_client, monkeypatch):
    admin = authenticated_client
    monkeypatch.setattr(auth, "clock", lambda: 100.0)
    with db.connect() as conn:
        member = conn.execute(
            "INSERT INTO users(email,name,password_hash,role_id) VALUES ('member@test','Member','unused',%s)"
            " RETURNING id,session_version", (role_id("Developer"),)).fetchone()
    with TestClient(app, base_url="http://localhost", cookies={auth.COOKIE: auth.sign_token(member)}) as client:
        assert client.post("/api/users/1/reset-password", headers=GUI).status_code == 403
    assert admin.post("/api/users/1/reset-password", headers=GUI).status_code == 400
    assert admin.post("/api/users/999999/reset-password", headers=GUI).status_code == 404
    auth.buckets[("create_user", 1)] = (auth.deque([100.0] * 9), 3600)
    reset_url = f"/api/users/{member['id']}/reset-password"
    assert admin.post(reset_url, headers=GUI).status_code == 200
    for url in ("/api/users", reset_url):
        response = admin.post(url, json={}, headers=GUI)
        assert response.status_code == 429 and response.headers["Retry-After"] == "3600"


def test_voluntary_password_change_validation_and_rate_limit(authenticated_client, monkeypatch):
    client = authenticated_client
    monkeypatch.setattr(auth, "clock", lambda: 100.0)
    for current, new, detail in (
        ("wrong", STRONG_PASSWORD, "Current password is incorrect"),
        (PASSWORD, PASSWORD, "New password must differ from the current password"),
        (PASSWORD, "Aa1!short", "Password must be 12 to 128 characters"),
    ):
        response = change_password(client, current, new)
        assert response.status_code == 400 and response.json() == {"detail": detail}
    assert client.get("/api/auth/me").json()["session_version"] == 0
    old_cookie = client.cookies.get(auth.COOKIE)
    assert change_password(client, PASSWORD, STRONG_PASSWORD).status_code == 200
    with TestClient(app, base_url="http://localhost", cookies={auth.COOKIE: old_cookie}) as stale:
        assert stale.get("/api/auth/me").status_code == 401
    assert login(client, EMAIL, STRONG_PASSWORD).status_code == 200
    assert change_password(client, STRONG_PASSWORD, STRONG_PASSWORD).status_code == 400
    response = change_password(client, STRONG_PASSWORD, "Different!Pass7")
    assert response.status_code == 429 and response.headers["Retry-After"] == "900"


@pytest.mark.parametrize("path,field,body", [
    ("/api/users", "password", {"email": "new@example.test", "name": "New", "role_id": 1}),
    ("/api/auth/password", "current_password", {"new_password": STRONG_PASSWORD}),
    ("/api/auth/login", "password", {"email": EMAIL}),
])
def test_credential_validation_does_not_echo_password(authenticated_client, path, field, body):
    password = "plaintext-secret-marker" * 50
    response = authenticated_client.post(path, headers=GUI, json={**body, field: password})
    assert response.status_code == 422
    detail, = response.json()["detail"]
    assert detail["loc"] == ["body", field] and detail["msg"] and detail["type"]
    assert "plaintext-secret-marker" not in response.text


def test_password_change_rejects_invalid_unicode(authenticated_client):
    client = authenticated_client
    response = client.post("/api/auth/password", headers={**GUI, "Content-Type": "application/json"},
                           content=json.dumps({"current_password": PASSWORD, "new_password": STRONG_PASSWORD + "\ud800"}))
    assert response.status_code == 400
    assert response.json() == {"detail": "Password contains an invalid Unicode character"}
    assert client.get("/api/auth/me").json()["session_version"] == 0
    assert login(client, EMAIL, PASSWORD).status_code == 200


def test_reset_wins_race_with_password_change(authenticated_client, monkeypatch):
    admin = authenticated_client
    created = admin.post("/api/users", headers=GUI, json={
        "email": "member@example.test", "name": "Member", "role_id": role_id("Developer"),
    }).json()
    original = created["temporary_password"]
    with TestClient(app, base_url="http://localhost") as member:
        assert login(member, created["email"], original).status_code == 200
        verify = auth.verify_password
        reset = []

        def reset_after_verify(password, stored):
            valid = verify(password, stored)
            if password == original:
                result = admin.post(f"/api/users/{created['id']}/reset-password", headers=GUI)
                assert result.status_code == 200
                reset.append(result.json()["temporary_password"])
            return valid

        with monkeypatch.context() as patch:
            patch.setattr(auth, "verify_password", reset_after_verify)
            response = change_password(member, original, STRONG_PASSWORD)
        assert response.status_code == 401 and "set-cookie" not in response.headers
        assert member.get("/api/auth/me").status_code == 401
        assert login(member, created["email"], STRONG_PASSWORD).status_code == 401
        signed_in = login(member, created["email"], reset[0])
        assert signed_in.status_code == 200 and signed_in.json()["must_change_password"] is True
        assert signed_in.json()["session_version"] == 1


def test_roles_crud_counts_locked_names_and_errors(authenticated_client):
    client = authenticated_client
    initial = client.get("/api/roles").json()
    assert initial == [
        {"id": role_id("Owner"), "name": "Owner", "routes": list(auth.ROUTES), "locked": True, "users": 1},
        {"id": role_id("Developer"), "name": "Developer", "routes": ["review", "scraping"], "locked": False, "users": 0},
    ]
    role = new_role(client, name=" Accounting ", routes=["review"])
    assert role["name"] == "Accounting" and role["users"] == 0
    assert client.post("/api/roles", headers=GUI, json={"name": "ACCOUNTING", "routes": ["review"]}).status_code == 409
    assert client.patch(f"/api/roles/{role['id']}", headers=GUI,
                        json={"name": "developer", "routes": ["review"]}).status_code == 409
    changed = client.patch(f"/api/roles/{role['id']}", headers=GUI,
                           json={"name": "Administrator", "routes": ["accounts"]})
    assert changed.status_code == 200 and changed.json()["routes"] == ["accounts"]
    with db.connect() as conn:
        conn.execute("INSERT INTO users(email,name,password_hash,role_id,active) VALUES ('inactive@test','Inactive','unused',%s,false)", (role["id"],))
    assert client.get("/api/roles").json()[-1]["users"] == 1
    assert client.delete(f"/api/roles/{role['id']}", headers=GUI).status_code == 409
    for method in ("PATCH", "DELETE"):
        assert client.request(method, f"/api/roles/{role_id('Owner')}", headers=GUI,
                              json={"name": "Unlocked", "routes": ["review"]}).status_code == 400
        assert client.request(method, "/api/roles/999999", headers=GUI,
                              json={"name": "Missing", "routes": ["review"]}).status_code == 404
    with db.connect() as conn:
        conn.execute("DELETE FROM users WHERE role_id=%s", (role["id"],))
    assert client.delete(f"/api/roles/{role['id']}", headers=GUI).json() == {"ok": True}
    assert client.get("/api/roles").json() == initial
    for name in ("", "  ", "x" * 201):
        assert client.post("/api/roles", headers=GUI, json={"name": name, "routes": ["accounts"]}).status_code == 422


def test_user_assignment_validation_and_self_lockout(authenticated_client):
    client = authenticated_client
    developer = role_id("Developer")
    body = {"email": "new@example.test", "name": "New", "role_id": developer}
    for change in ({"role_id": 999999},):
        assert client.post("/api/users", headers=GUI, json={**body, **change}).status_code == 404
    for change in ({"role": "admin"}, {"routes": ["accounts"]}, {"role_id": None}):
        assert client.post("/api/users", headers=GUI, json={**body, **change}).status_code == 422
    created = client.post("/api/users", headers=GUI, json=body).json()
    url = f"/api/users/{created['id']}"
    assert client.patch(url, headers=GUI, json={"role_id": 999999}).status_code == 404
    assert client.patch("/api/users/999999", headers=GUI, json={"role_id": developer}).status_code == 404
    for invalid in ({}, {"routes": ["review"]}, {"role_id": 0}):
        assert client.patch(url, headers=GUI, json=invalid).status_code == 422
    response = client.patch("/api/users/1", headers=GUI, json={"role_id": developer})
    assert response.status_code == 400 and response.json()["detail"] == "You would lose access to Accounts"
    assert client.patch(url, headers=GUI, json={"role_id": role_id("Owner")}).status_code == 200
    assert client.get("/api/users").json()[-1]["role"]["name"] == "Owner"
    # Reassigning an account changes its grants without minting another cookie.
    with TestClient(app, base_url="http://localhost") as member:
        assert login(member, created["email"], created["temporary_password"]).status_code == 200
        assert change_password(member, created["temporary_password"], "Another!Pass3").status_code == 200
        assert member.get("/api/roles").status_code == 200
        assert client.patch(url, headers=GUI, json={"role_id": developer}).status_code == 200
        assert member.get("/api/roles").status_code == 403


def test_shared_role_edit_cannot_remove_own_accounts_access(authenticated_client):
    client = authenticated_client
    role = new_role(client, name="Administrator", routes=["accounts"])
    with db.connect() as conn:
        actor = conn.execute("INSERT INTO users(email,name,password_hash,role_id) VALUES ('manager@test','Manager','unused',%s) RETURNING id,session_version",
                             (role["id"],)).fetchone()
        conn.execute("INSERT INTO users(email,name,password_hash,role_id) VALUES ('coworker@test','Coworker','unused',%s)", (role["id"],))
    manager = TestClient(app, base_url="http://localhost", cookies={auth.COOKIE: auth.sign_token(actor)})
    url = f"/api/roles/{role['id']}"
    denied = manager.patch(url, headers=GUI, json={"name": "Accounting", "routes": ["review"]})
    assert denied.status_code == 400 and denied.json()["detail"] == "You would lose access to Accounts"
    assert manager.get("/api/roles").json()[-1]["routes"] == ["accounts"]
    assert manager.patch(f"/api/users/{actor['id']}", headers=GUI, json={"role_id": role_id("Developer")}).status_code == 400
    manager.close()


def test_inactive_owners_do_not_allow_last_active_owner_demotion(authenticated_client):
    client = authenticated_client
    role = new_role(client, name="Administrator", routes=["accounts"])
    with db.connect() as conn:
        conn.execute("INSERT INTO users(email,name,password_hash,role_id,active) VALUES ('inactive@test','Inactive','unused',%s,false)", (role_id("Owner"),))
        actor = conn.execute("INSERT INTO users(email,name,password_hash,role_id) VALUES ('manager@test','Manager','unused',%s) RETURNING id,session_version",
                             (role["id"],)).fetchone()
    manager = TestClient(app, base_url="http://localhost", cookies={auth.COOKIE: auth.sign_token(actor)})
    denied = manager.patch("/api/users/1", headers=GUI, json={"role_id": role["id"]})
    assert denied.status_code == 400 and denied.json()["detail"] == "Cannot leave zero active Owner accounts"
    manager.close()


def test_concurrent_demotions_leave_one_active_owner(authenticated_client):
    role = new_role(authenticated_client, name="Administrator", routes=["accounts"])
    owner = role_id("Owner")
    with db.connect() as conn:
        second = conn.execute("INSERT INTO users(email,name,password_hash,role_id) VALUES ('second@test','Second','unused',%s) RETURNING id", (owner,)).fetchone()["id"]
        actor = conn.execute("INSERT INTO users(email,name,password_hash,role_id) VALUES ('manager@test','Manager','unused',%s) RETURNING id,session_version", (role["id"],)).fetchone()
    barrier = threading.Barrier(2)

    def demote(uid):
        with TestClient(app, base_url="http://localhost", cookies={auth.COOKIE: auth.sign_token(actor)}) as client:
            barrier.wait(timeout=10)
            return client.patch(f"/api/users/{uid}", headers=GUI, json={"role_id": role["id"]}).status_code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(demote, [1, second])) == [200, 400]
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM users WHERE active AND role_id=%s", (owner,)).fetchone()["n"] == 1


@pytest.mark.parametrize("password,message", [
    (" " * 12, "blank"), ("Aa1!" + "x" * 7, "12 to 128"), ("Aa1!" + "x" * 125, "12 to 128"),
    ("aa1!" + "x" * 8, "uppercase"), ("AA1!" + "X" * 8, "lowercase"),
    ("Aaaa!" + "x" * 7, "digit"), ("Aaa1é " + "x" * 6, "symbol"), ("AdMiN!123abc", "before @"),
])
def test_password_policy_rejects_each_rule(password, message):
    assert message in auth.validate_password(password, EMAIL)


@pytest.mark.parametrize("length", [12, 128])
def test_password_policy_accepts_length_boundaries_and_short_local_parts(length):
    assert auth.validate_password("Aa1!" + "x" * (length - 4), "aa@example.test") is None


def test_password_hash_is_salted_and_bad_hashes_do_not_raise():
    first, second = auth.hash_password(PASSWORD), auth.hash_password(PASSWORD)
    assert first != second and auth.verify_password(PASSWORD, first)
    assert not auth.verify_password("wrong", first)
    for value in ("", "bad", "scrypt$x$y", "scrypt$" + "z" * 32 + "$" + "z" * 128):
        assert not auth.verify_password(PASSWORD, value)


def test_sliding_window_retry_cleanup_and_cap(monkeypatch):
    monkeypatch.setattr(auth, "buckets", {})
    monkeypatch.setattr(auth, "last_sweep", 0)
    now = [100.0]
    monkeypatch.setattr(auth, "clock", lambda: now[0])
    monkeypatch.setattr(auth, "MAX_BUCKETS", 2)
    auth.hit("a", "one", 2, 10)
    now[0] += 5
    auth.hit("a", "one", 2, 10)
    with pytest.raises(HTTPException) as error:
        auth.hit("a", "one", 2, 10)
    assert error.value.status_code == 429 and error.value.headers["Retry-After"] == "5"
    auth.hit("b", "two", 1, 60)
    with pytest.raises(HTTPException):
        auth.hit("c", "three", 1, 10)
    assert len(auth.buckets) == 2
    now[0] = 110
    auth.hit("a", "one", 2, 10)
    now[0] = 170
    auth.hit("c", "three", 1, 10)
    assert list(auth.buckets) == [("c", "three")]


def test_limiter_is_atomic(monkeypatch):
    monkeypatch.setattr(auth, "buckets", {})
    def attempt(_):
        try:
            auth.hit("thread", "same", 5, 900)
            return True
        except HTTPException:
            return False
    with ThreadPoolExecutor(10) as pool:
        assert sum(pool.map(attempt, range(20))) == 5


def test_login_limits_ignore_forwarded_ips_and_expire(authenticated_client, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(auth, "clock", lambda: now[0])
    client = TestClient(app, base_url="http://localhost")
    for i in range(5):
        response = client.post("/api/auth/login", json={"email": EMAIL.upper() if i % 2 else EMAIL, "password": "wrong"},
                               headers={**GUI, "X-Forwarded-For": f"192.0.2.{i}"})
        assert response.status_code == 401
    response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=GUI)
    assert response.status_code == 429 and response.headers["Retry-After"] == "900"
    now[0] += 900
    assert client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=GUI).status_code == 200
    auth.buckets.clear()
    for i in range(30):
        assert client.post("/api/auth/login", json={"email": f"unknown{i}@example.test", "password": "wrong"},
                           headers={**GUI, "X-Forwarded-For": f"192.0.2.{i}"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=GUI).status_code == 429


@pytest.mark.parametrize("same_email,limit", [(True, 5), (False, 30)])
def test_entrypoint_limits_ignore_forwarded_ips(authenticated_client, monkeypatch, same_email, limit):
    settings = {}
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    monkeypatch.setattr(auth, "clock", lambda: 100.0)
    monkeypatch.setattr(uvicorn, "run", lambda application, **kwargs: settings.update(app=application, **kwargs))
    runpy.run_module("scraper.app", run_name="__main__")
    config = uvicorn.Config(**settings)
    config.load()
    with TestClient(config.loaded_app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        for i in range(limit + 1):
            response = client.post("/api/auth/login", json={
                "email": EMAIL if same_email else f"unknown{i}@example.test", "password": "wrong"},
                headers={**GUI, "X-Forwarded-For": f"192.0.2.{i + 1}"})
            assert response.status_code == (401 if i < limit else 429)
        assert response.headers["Retry-After"] == "900"


def test_user_and_api_limits_are_per_user(authenticated_client, monkeypatch):
    monkeypatch.setattr(auth, "clock", lambda: 100.0)
    auth.buckets[("create_user", 1)] = (auth.deque([100.0] * 10), 3600)
    response = authenticated_client.post("/api/users", json={}, headers=GUI)
    assert response.status_code == 429 and response.headers["Retry-After"] == "3600"
    assert authenticated_client.get("/api/auth/me").status_code == 200
    auth.buckets[("api", 1)] = (auth.deque([100.0] * 300), 60)
    response = authenticated_client.get("/api/auth/me")
    assert response.status_code == 429 and response.headers["Retry-After"] == "60"
    response = authenticated_client.put("/api/schedule", headers={**GUI, "X-Expected-User": "2"},
                                        json={"enabled": True, "interval_minutes": 120})
    assert response.status_code == 429 and response.headers["Retry-After"] == "60"
    # A second user has an independent API bucket.
    with db.connect() as conn:
        second = conn.execute("INSERT INTO users(email,name,password_hash,role_id) SELECT 'two@example.test','Two','unused',id FROM roles WHERE name='Developer' RETURNING id,session_version").fetchone()
    authenticated_client.cookies.set(auth.COOKIE, auth.sign_token(second), domain="localhost.local", path="/")
    assert authenticated_client.get("/api/auth/me").status_code == 200


@pytest.mark.parametrize("secret", [None, "", "short", "x" * 31])
def test_bad_secret_fails_before_database_access(monkeypatch, secret):
    if secret is None:
        monkeypatch.delenv("JWT_SECRET", raising=False)
    else:
        monkeypatch.setenv("JWT_SECRET", secret)
    def forbidden():
        pytest.fail("Bad JWT config must fail before any database access")
    monkeypatch.setattr(db, "connect", forbidden)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        with TestClient(app, base_url="http://localhost"):
            pass
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        jwt_secret()


@pytest.mark.parametrize("password", ["x", "x" * 1024, ""])
def test_first_admin_seed_only_when_empty(authenticated_client, monkeypatch, caplog, password):
    monkeypatch.setenv("ADMIN_EMAIL", "seed@example.test")
    monkeypatch.setenv("ADMIN_PASSWORD", password)
    with db.connect() as conn:
        conn.execute("DELETE FROM users")
    with TestClient(app, base_url="http://localhost"):
        pass
    with db.connect() as conn:
        users = conn.execute("SELECT * FROM users").fetchall()
    if password:
        assert len(users) == 1 and users[0]["email"] == "seed@example.test" and users[0]["role_id"] == role_id("Owner")
        assert users[0]["must_change_password"] is False and users[0]["session_version"] == 0
        assert auth.verify_password(password, users[0]["password_hash"])
        assert authenticated_client.post("/api/auth/login", json={
            "email": "seed@example.test", "password": password}, headers=GUI).status_code == 200
        monkeypatch.setenv("ADMIN_EMAIL", "another@example.test")
        with TestClient(app, base_url="http://localhost"):
            pass
        with db.connect() as conn:
            assert conn.execute("SELECT * FROM users").fetchall() == users
    else:
        assert users == [] and "Users table is empty" in caplog.text


def test_invalid_bootstrap_password_is_not_in_startup_traceback(authenticated_client, monkeypatch, caplog):
    password = "secret123" + "x" * 1016
    monkeypatch.setenv("ADMIN_EMAIL", "seed@example.test")
    monkeypatch.setenv("ADMIN_PASSWORD", password)
    with db.connect() as conn:
        conn.execute("DELETE FROM users")
    with pytest.raises(ValidationError) as error:
        with TestClient(app, base_url="http://localhost"):
            pass
    assert "password" in str(error.value) and "at most 1024" in str(error.value)
    assert "secret123" not in "".join(traceback.format_exception(error.value))
    assert "secret123" not in caplog.text
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 0


def test_seeded_admin_can_be_reset_and_restart_preserves_reset(authenticated_client, monkeypatch):
    admin = authenticated_client
    monkeypatch.setenv("ADMIN_EMAIL", "seed@example.test")
    monkeypatch.setenv("ADMIN_PASSWORD", "weak")
    owner = role_id("Owner")
    with db.connect() as conn:
        conn.execute("DELETE FROM users")
    with TestClient(app, base_url="http://localhost") as seeded:
        signed_in = login(seeded, "seed@example.test", "weak")
        assert signed_in.status_code == 200 and signed_in.json()["must_change_password"] is False
        seed_id = signed_in.json()["id"]
        assert change_password(seeded, "weak", "alsoweak").status_code == 400
        with db.connect() as conn:
            conn.execute("INSERT INTO users(email,name,password_hash,role_id) VALUES (%s,'Admin',%s,%s)",
                         (EMAIL, auth.hash_password(PASSWORD), owner))
        assert login(admin, EMAIL, PASSWORD).status_code == 200
        reset = admin.post(f"/api/users/{seed_id}/reset-password", headers=GUI)
        assert reset.status_code == 200 and seeded.get("/api/auth/me").status_code == 401
    with TestClient(app, base_url="http://localhost") as restarted:
        assert login(restarted, "seed@example.test", "weak").status_code == 401
        signed_in = login(restarted, "seed@example.test", reset.json()["temporary_password"])
        assert signed_in.status_code == 200 and signed_in.json()["must_change_password"] is True
        assert signed_in.json()["session_version"] == 1


@pytest.mark.parametrize("error", [RuntimeError, ValueError])
def test_unexpected_errors_are_generic_and_logged(authenticated_client, monkeypatch, caplog, error):
    def broken():
        raise error("private credentials and query details")
    monkeypatch.setattr(db, "connect", broken)
    client = TestClient(app, base_url="http://localhost", cookies=authenticated_client.cookies,
                        raise_server_exceptions=False)
    response = client.get("/api/rules")
    assert response.status_code == 500 and response.json() == {"detail": "Internal server error"}
    assert "private credentials and query details" in caplog.text
