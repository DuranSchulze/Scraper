"""Authentication uses fixture accounts and disposable local Postgres only."""

import time
import runpy
import traceback
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
        conn.execute("INSERT INTO users(email,name,password_hash,role) VALUES (%s,'Admin',%s,'admin')",
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
    assert user == {"id": 1, "email": EMAIL, "name": "Admin", "role": "admin"}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Max-Age=28800" in cookie and "Secure" not in cookie
    claims = jwt.decode(client.cookies.get(auth.COOKIE), SECRET, algorithms=["HS256"])
    assert claims["sub"] == "1" and claims["role"] == "admin" and claims["exp"] - claims["iat"] == 28800
    assert client.get("/api/auth/me").json() == user
    response = client.post("/api/auth/logout", headers=GUI)
    assert response.json() == {"ok": True}
    assert all(value in response.headers["set-cookie"] for value in ("Max-Age=0", "HttpOnly", "SameSite=lax", "Path=/"))
    assert client.get("/api/auth/me").status_code == 401


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
                      "demoted": "UPDATE users SET role='member'"}[change])
    response = authenticated_client.get("/api/auth/me")
    if change == "demoted":
        assert response.status_code == 200 and response.json()["role"] == "member"
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


def test_users_create_list_and_case_insensitive_duplicates(authenticated_client):
    body = {"email": "Member@Example.test", "name": " Member ", "password": PASSWORD, "role": "member"}
    response = authenticated_client.post("/api/users", json=body, headers=GUI)
    assert response.status_code == 201
    created = response.json()
    assert created["email"] == "member@example.test" and created["name"] == "Member" and created["active"] is True
    assert set(created) == {"id", "email", "name", "role", "active", "created_at"}
    users = authenticated_client.get("/api/users").json()
    assert len(users) == 2 and all("password_hash" not in user for user in users)
    assert authenticated_client.post("/api/users", json=body, headers=GUI).status_code == 409
    with db.connect() as conn:
        stored = conn.execute("SELECT password_hash FROM users WHERE id=%s", (created["id"],)).fetchone()["password_hash"]
    assert stored != PASSWORD and auth.verify_password(PASSWORD, stored)


@pytest.mark.parametrize("change", [{"email": "invalid"}, {"email": "a@b"}, {"email": "a@bad_domain.test"},
                                    {"email": "a@-bad.test"}, {"email": "a..b@example.test"}, {"name": " "},
                                    {"password": ""}, {"password": "x" * 1025}, {"role": "owner"}])
def test_user_validation(authenticated_client, change):
    body = {"email": "new@example.test", "name": "New", "password": PASSWORD, "role": "member", **change}
    response = authenticated_client.post("/api/users", json=body, headers=GUI)
    assert response.status_code == 422 and isinstance(response.json()["detail"], list)
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 1


@pytest.mark.parametrize("password", ["x", "x" * 1024])
def test_created_user_can_log_in_with_password_length_boundaries(authenticated_client, password):
    body = {"email": "new@example.test", "name": "New", "password": password, "role": "member"}
    assert authenticated_client.post("/api/users", json=body, headers=GUI).status_code == 201
    response = authenticated_client.post("/api/auth/login", json={"email": body["email"], "password": password}, headers=GUI)
    assert response.status_code == 200 and response.json()["email"] == body["email"]


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
    # A second user has an independent API bucket.
    with db.connect() as conn:
        second = conn.execute("INSERT INTO users(email,name,password_hash,role) VALUES ('two@example.test','Two','unused','member') RETURNING id,role").fetchone()
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
        assert len(users) == 1 and users[0]["email"] == "seed@example.test" and users[0]["role"] == "admin"
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
