"""Cookie sessions, account management and in-process request limits."""

from collections import deque
from datetime import datetime
import hashlib
import hmac
import logging
import math
import re
import secrets
import string
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
import jwt
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from . import db
from .config import env, jwt_secret

COOKIE = "scraper_session"
ROUTES = ("review", "scraping", "accounts")
USER_SELECT = ("SELECT u.id,u.email,u.name,u.active,u.created_at,"
               " u.must_change_password,u.session_version,"
               " jsonb_build_object('id',r.id,'name',r.name) AS role,r.routes"
               " FROM users u JOIN roles r ON r.id=u.role_id")
ROLE_SELECT = ("SELECT r.id,r.name,r.routes,r.locked,"
               " (SELECT count(*) FROM users u WHERE u.role_id=r.id) AS users FROM roles r")
SESSION_SECONDS = 8 * 60 * 60
MAX_BUCKETS = 10_000
buckets = {}
last_sweep = 0
clock = time.monotonic
limit_lock = threading.Lock()


def hit(bucket, key, limit, window_seconds):
    global last_sweep
    now = clock()
    with limit_lock:
        if now - last_sweep >= 60 or len(buckets) >= MAX_BUCKETS:
            for identity, (hits, window) in list(buckets.items()):
                while hits and hits[0] <= now - window:
                    hits.popleft()
                if not hits:
                    del buckets[identity]
            last_sweep = now
        identity = (bucket, key)
        if identity not in buckets:
            if len(buckets) >= MAX_BUCKETS:
                raise HTTPException(429, "Too many requests", headers={"Retry-After": "60"})
            buckets[identity] = (deque(), window_seconds)
        hits, _ = buckets[identity]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            retry = max(1, math.ceil(hits[0] + window_seconds - now))
            raise HTTPException(429, "Too many requests", headers={"Retry-After": str(retry)})
        hits.append(now)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=5, dklen=64)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt" or len(salt) != 32 or len(digest) != 128:
            return False
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=5, dklen=64)
        return hmac.compare_digest(candidate, bytes.fromhex(digest))
    except (ValueError, TypeError, AttributeError):
        return False


DUMMY_HASH = hash_password("unused login comparison")


def validate_password(password: str, email: str) -> str | None:
    if not password.strip():
        return "Password cannot be blank"
    if not 12 <= len(password) <= 128:
        return "Password must be 12 to 128 characters"
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        return "Password contains an invalid Unicode character"
    if not re.search(r"[A-Z]", password):
        return "Password must contain an uppercase letter"
    if not re.search(r"[a-z]", password):
        return "Password must contain a lowercase letter"
    if not re.search(r"[0-9]", password):
        return "Password must contain a digit"
    if not any(not char.isalnum() and not char.isspace() for char in password):
        return "Password must contain a symbol"
    local = email.split("@", 1)[0]
    if len(local) >= 3 and local.casefold() in password.casefold():
        return "Password must not contain the part of your email before @"
    return None


def temporary_password(email: str) -> str:
    alphabet = string.ascii_letters + string.digits + string.punctuation
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(16))
        if validate_password(password, email) is None:
            return password


def sign_token(user: dict) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user["id"]), "iat": now,
                       "exp": now + SESSION_SECONDS, "ver": user["session_version"]},
                      jwt_secret(), algorithm="HS256")


def verify_token(token: str) -> dict | None:
    try:
        claims = jwt.decode(token, jwt_secret(), algorithms=["HS256"],
                            options={"require": ["sub", "iat", "exp"]})
        subject = claims["sub"]
        if (not isinstance(subject, str) or not subject.isascii() or not subject.isdigit()
                or len(subject) > 19 or not 0 < int(subject) <= 9223372036854775807):
            return None
        version = claims.setdefault("ver", 0)
        if type(version) is not int or not 0 <= version <= 2147483647:
            return None
        return claims
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError, RuntimeError):
        return None


def current_user(request: Request):
    if hasattr(request.state, "auth_user"):
        return request.state.auth_user
    method, path = request.method, request.url.path
    if not path.startswith("/api/") or (method, path) == ("POST", "/api/auth/login"):
        return None
    claims = verify_token(request.cookies.get(COOKIE, ""))
    if not claims:
        raise HTTPException(401, "Sign in required")
    with db.connect() as conn:
        user = conn.execute(USER_SELECT + " WHERE u.id=%s AND u.active", (int(claims["sub"]),)).fetchone()
    if not user or claims["ver"] != user["session_version"]:
        raise HTTPException(401, "Sign in required")
    if user["must_change_password"] and (method, path) not in {
        ("GET", "/api/auth/me"), ("POST", "/api/auth/logout"), ("POST", "/api/auth/password"),
    }:
        raise HTTPException(403, "Password change required")
    if method == "POST" and (path == "/api/users" or re.fullmatch(r"/api/users/[^/]+/reset-password", path)):
        hit("create_user", user["id"], 10, 3600)
    else:
        hit("api", user["id"], 300, 60)
    if method in {"POST", "PUT", "PATCH", "DELETE"} and path != "/api/auth/logout":
        expected = request.headers.get("X-Expected-User")
        if expected is not None and expected != str(user["id"]):
            raise HTTPException(409, "The signed-in account changed in another tab. Nothing was saved; reload the page.")
    return user


async def authenticate_before_body(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        try:
            request.state.auth_user = await run_in_threadpool(current_user, request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
    return await call_next(request)


def require_route(key):
    if key not in ROUTES:
        raise ValueError("Unknown route")

    def permitted(user=Depends(current_user)):
        if key not in user["routes"]:
            raise HTTPException(403, "You don't have access to this page")
        return user

    return permitted


class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(
                r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}", value):
            raise ValueError("Enter a valid email address")
        local = value.split("@", 1)[0]
        if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local:
            raise ValueError("Enter a valid email address")
        return value


class LoginIn(EmailIn):
    model_config = ConfigDict(hide_input_in_errors=True)

    password: str = Field(min_length=1, max_length=1024)


class PasswordIn(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: SecretStr


class RoutesIn(BaseModel):
    routes: list[str] = Field(min_length=1)

    @field_validator("routes")
    @classmethod
    def allowed_routes(cls, value):
        if any(key not in ROUTES for key in value):
            raise ValueError("Unknown route")
        return [key for key in ROUTES if key in value]


class RoleIn(RoutesIn):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("Name is required")
        return value.strip()


class UserRoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: int = Field(gt=0)


class UserIn(EmailIn, UserRoleIn):
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")

    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("Name is required")
        return value.strip()


class RoleRef(BaseModel):
    id: int
    name: str


class Role(RoleRef):
    routes: list[str]
    locked: bool
    users: int


class User(BaseModel):
    id: int
    email: str
    name: str
    role: RoleRef
    routes: list[str]
    must_change_password: bool
    session_version: int

    @field_validator("routes")
    @classmethod
    def ordered_routes(cls, value):
        return [key for key in ROUTES if key in value]


class Account(User):
    active: bool
    created_at: datetime


class TemporaryPassword(BaseModel):
    temporary_password: str


class CreatedAccount(Account, TemporaryPassword):
    pass


class CredentialRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()
        if "POST" not in self.methods or self.path not in {"/api/auth/login", "/api/auth/password", "/api/users"}:
            return handle

        async def validate_without_echo(request: Request):
            try:
                return await handle(request)
            except RequestValidationError as exc:
                detail = [{key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()]
                return JSONResponse({"detail": detail}, status_code=422)

        return validate_without_echo


router = APIRouter(prefix="/api", route_class=CredentialRoute)


def cookie_samesite():
    value = env("COOKIE_SAMESITE", "lax")
    return value if value in ("lax", "none", "strict") else "lax"


def set_session_cookie(request: Request, response: Response, user: dict):
    samesite = cookie_samesite()
    response.set_cookie(COOKIE, sign_token(user), max_age=SESSION_SECONDS, httponly=True,
                        secure=samesite == "none" or request.url.scheme == "https" or env("APP_URL").startswith("https://"),
                        samesite=samesite, path="/")


@router.post("/auth/login", response_model=User)
def login(body: LoginIn, request: Request, response: Response):
    peer = request.client.host if request.client else "unknown"
    hit("login_ip", peer, 30, 900)
    hit("login_email", (peer, body.email), 5, 900)
    with db.connect() as conn:
        user = conn.execute(USER_SELECT.replace("SELECT u.id", "SELECT u.password_hash,u.id") + " WHERE lower(u.email)=%s",
                            (body.email,)).fetchone()
    valid = verify_password(body.password, user["password_hash"] if user else DUMMY_HASH)
    if not valid or not user or not user["active"]:
        raise HTTPException(401, "Invalid email or password")
    set_session_cookie(request, response, user)
    return user


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    samesite = cookie_samesite()
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite=samesite,
                           secure=samesite == "none" or request.url.scheme == "https" or env("APP_URL").startswith("https://"))
    return {"ok": True}


@router.get("/auth/me", response_model=User)
def me(user=Depends(current_user)):
    return user


@router.post("/auth/password", response_model=User)
def change_password(body: PasswordIn, request: Request, response: Response, user=Depends(current_user)):
    hit("change_password", user["id"], 5, 900)
    current = body.current_password.get_secret_value()
    new = body.new_password.get_secret_value()
    params = (user["id"], user["session_version"])
    with db.connect() as conn:
        credentials = conn.execute(
            "SELECT password_hash FROM users WHERE id=%s AND session_version=%s AND active", params).fetchone()
        if not credentials:
            raise HTTPException(401, "Sign in required")
        if not verify_password(current, credentials["password_hash"]):
            raise HTTPException(400, "Current password is incorrect")
        if new == current:
            raise HTTPException(400, "New password must differ from the current password")
        if error := validate_password(new, user["email"]):
            raise HTTPException(400, error)
        updated = conn.execute(
            "UPDATE users SET password_hash=%s,must_change_password=false,session_version=session_version+1"
            " WHERE id=%s AND session_version=%s AND active RETURNING must_change_password,session_version",
            (hash_password(new), *params)).fetchone()
        if not updated:
            raise HTTPException(401, "Sign in required")
    user = {**user, **updated}
    set_session_cookie(request, response, user)
    return user


def _lock_accounts(conn, user):
    # One lock order serializes role edits, assignments and Owner-count checks.
    conn.execute("LOCK TABLE roles, users IN SHARE ROW EXCLUSIVE MODE")
    actor = conn.execute(USER_SELECT + " WHERE u.id=%s AND u.active", (user["id"],)).fetchone()
    if not actor or actor["session_version"] != user["session_version"]:
        raise HTTPException(401, "Sign in required")
    if "accounts" not in actor["routes"]:
        raise HTTPException(403, "You don't have access to this page")
    return actor


@router.get("/roles", response_model=list[Role])
def roles(user=Depends(require_route("accounts"))):
    with db.connect() as conn:
        return conn.execute(ROLE_SELECT + " ORDER BY r.id").fetchall()


@router.post("/roles", response_model=Role, status_code=201)
def create_role(body: RoleIn, user=Depends(require_route("accounts"))):
    try:
        with db.connect() as conn:
            _lock_accounts(conn, user)
            rid = conn.execute("INSERT INTO roles(name,routes) VALUES (%s,%s) RETURNING id",
                               (body.name, body.routes)).fetchone()["id"]
            return conn.execute(ROLE_SELECT + " WHERE r.id=%s", (rid,)).fetchone()
    except UniqueViolation as exc:
        raise HTTPException(409, "Role name already exists") from exc


@router.patch("/roles/{role_id}", response_model=Role)
def update_role(role_id: int, body: RoleIn, user=Depends(require_route("accounts"))):
    try:
        with db.connect() as conn:
            actor = _lock_accounts(conn, user)
            role = conn.execute("SELECT locked FROM roles WHERE id=%s", (role_id,)).fetchone()
            if not role:
                raise HTTPException(404, "Role not found")
            if role["locked"]:
                raise HTTPException(400, "Locked role cannot be edited or deleted")
            if actor["role"]["id"] == role_id and "accounts" not in body.routes:
                raise HTTPException(400, "You would lose access to Accounts")
            conn.execute("UPDATE roles SET name=%s,routes=%s WHERE id=%s", (body.name, body.routes, role_id))
            return conn.execute(ROLE_SELECT + " WHERE r.id=%s", (role_id,)).fetchone()
    except UniqueViolation as exc:
        raise HTTPException(409, "Role name already exists") from exc


@router.delete("/roles/{role_id}")
def delete_role(role_id: int, user=Depends(require_route("accounts"))):
    with db.connect() as conn:
        _lock_accounts(conn, user)
        role = conn.execute(ROLE_SELECT + " WHERE r.id=%s", (role_id,)).fetchone()
        if not role:
            raise HTTPException(404, "Role not found")
        if role["locked"]:
            raise HTTPException(400, "Locked role cannot be edited or deleted")
        if role["users"]:
            raise HTTPException(409, "Role is in use")
        conn.execute("DELETE FROM roles WHERE id=%s", (role_id,))
    return {"ok": True}


@router.get("/users", response_model=list[Account])
def users(user=Depends(require_route("accounts"))):
    with db.connect() as conn:
        return conn.execute(USER_SELECT + " ORDER BY u.id").fetchall()


@router.post("/users", response_model=CreatedAccount, status_code=201)
def create_user(body: UserIn, response: Response, user=Depends(require_route("accounts"))):
    password = temporary_password(body.email)
    password_hash = hash_password(password)
    try:
        with db.connect() as conn:
            _lock_accounts(conn, user)
            if not conn.execute("SELECT 1 FROM roles WHERE id=%s", (body.role_id,)).fetchone():
                raise HTTPException(404, "Role not found")
            uid = conn.execute(
                "INSERT INTO users(email,name,password_hash,role_id,must_change_password)"
                " VALUES (%s,%s,%s,%s,true) RETURNING id",
                (body.email, body.name, password_hash, body.role_id)).fetchone()["id"]
            account = conn.execute(USER_SELECT + " WHERE u.id=%s", (uid,)).fetchone()
    except UniqueViolation as exc:
        raise HTTPException(409, "Email already exists") from exc
    response.headers["Cache-Control"] = "no-store"
    return {**account, "temporary_password": password}


@router.post("/users/{user_id}/reset-password", response_model=TemporaryPassword)
def reset_password(user_id: int, response: Response, user=Depends(require_route("accounts"))):
    if user_id == user["id"]:
        raise HTTPException(400, "Use Change password to reset your own password")
    with db.connect() as conn:
        _lock_accounts(conn, user)
        target = conn.execute("SELECT email FROM users WHERE id=%s", (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, "User not found")
        password = temporary_password(target["email"])
        conn.execute(
            "UPDATE users SET password_hash=%s,must_change_password=true,session_version=session_version+1 WHERE id=%s",
            (hash_password(password), user_id))
    response.headers["Cache-Control"] = "no-store"
    return {"temporary_password": password}


@router.patch("/users/{user_id}", response_model=Account)
def update_user_role(user_id: int, body: UserRoleIn, user=Depends(require_route("accounts"))):
    with db.connect() as conn:
        actor = _lock_accounts(conn, user)
        target = conn.execute("SELECT role_id,active FROM users WHERE id=%s FOR UPDATE", (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, "User not found")
        role = conn.execute("SELECT routes FROM roles WHERE id=%s", (body.role_id,)).fetchone()
        if not role:
            raise HTTPException(404, "Role not found")
        if user_id == actor["id"] and "accounts" not in role["routes"]:
            raise HTTPException(400, "You would lose access to Accounts")
        owner_id = conn.execute("SELECT id FROM roles WHERE locked AND name='Owner'").fetchone()["id"]
        if target["active"] and target["role_id"] == owner_id and body.role_id != owner_id:
            owners = conn.execute("SELECT count(*) AS n FROM users WHERE active AND role_id=%s", (owner_id,)).fetchone()["n"]
            if owners <= 1:
                raise HTTPException(400, "Cannot leave zero active Owner accounts")
        conn.execute("UPDATE users SET role_id=%s WHERE id=%s", (body.role_id, user_id))
        return conn.execute(USER_SELECT + " WHERE u.id=%s", (user_id,)).fetchone()


def seed_admin(conn):
    conn.execute("LOCK TABLE users IN EXCLUSIVE MODE")
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return
    email, password = env("ADMIN_EMAIL"), env("ADMIN_PASSWORD")
    if not email or not password:
        logging.getLogger(__name__).warning("Users table is empty; set ADMIN_EMAIL and ADMIN_PASSWORD to create the first admin")
        return
    owner_id = conn.execute("SELECT id FROM roles WHERE locked AND name='Owner'").fetchone()["id"]
    admin = LoginIn(email=email, password=password)
    conn.execute("INSERT INTO users(email,name,password_hash,role_id) VALUES (%s,%s,%s,%s)",
                 (admin.email, "Administrator", hash_password(admin.password), owner_id))
