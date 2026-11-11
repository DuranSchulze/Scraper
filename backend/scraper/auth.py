"""Cookie sessions, account management and in-process request limits."""

from collections import deque
from datetime import datetime
import hashlib
import hmac
import logging
import math
import re
import secrets
import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
import jwt
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import db
from .config import env, jwt_secret

COOKIE = "scraper_session"
ROUTES = ("review", "scraping")
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


def sign_token(user: dict) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user["id"]), "role": user["role"], "iat": now,
                       "exp": now + SESSION_SECONDS}, jwt_secret(), algorithm="HS256")


def verify_token(token: str) -> dict | None:
    try:
        claims = jwt.decode(token, jwt_secret(), algorithms=["HS256"],
                            options={"require": ["sub", "role", "iat", "exp"]})
        subject = claims["sub"]
        if (not isinstance(subject, str) or not subject.isascii() or not subject.isdigit()
                or len(subject) > 19 or not 0 < int(subject) <= 9223372036854775807
                or claims["role"] not in ("admin", "member")):
            return None
        return claims
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError, RuntimeError):
        return None


def current_user(request: Request):
    if not request.url.path.startswith("/api/") or (
            request.method == "POST" and request.url.path == "/api/auth/login"):
        return None
    claims = verify_token(request.cookies.get(COOKIE, ""))
    if not claims:
        raise HTTPException(401, "Sign in required")
    with db.connect() as conn:
        user = conn.execute("SELECT id,email,name,role,routes FROM users WHERE id=%s AND active", (int(claims["sub"]),)).fetchone()
    if not user:
        raise HTTPException(401, "Sign in required")
    if request.method == "POST" and request.url.path == "/api/users":
        hit("create_user", user["id"], 10, 3600)
    else:
        hit("api", user["id"], 300, 60)
    return user


def require_admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return user


def require_route(key):
    if key not in ROUTES:
        raise ValueError("Unknown route")

    def permitted(user=Depends(current_user)):
        if user["role"] != "admin" and key not in user["routes"]:
            raise HTTPException(403, "You don't have access to this page")
        return user

    return permitted


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)

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


class RoutesIn(BaseModel):
    routes: list[str] = Field(min_length=1)

    @field_validator("routes")
    @classmethod
    def allowed_routes(cls, value):
        if any(key not in ROUTES for key in value):
            raise ValueError("Unknown route")
        return [key for key in ROUTES if key in value]


class UserIn(LoginIn, RoutesIn):
    model_config = ConfigDict(hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)
    role: Literal["admin", "member"]
    routes: list[str] = Field(default_factory=lambda: list(ROUTES), min_length=1)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("Name is required")
        return value.strip()


class User(BaseModel):
    id: int
    email: str
    name: str
    role: Literal["admin", "member"]
    routes: list[str]

    @field_validator("routes")
    @classmethod
    def ordered_routes(cls, value):
        return [key for key in ROUTES if key in value]


class Account(User):
    active: bool
    created_at: datetime


router = APIRouter(prefix="/api")


def cookie_samesite():
    value = env("COOKIE_SAMESITE", "lax")
    return value if value in ("lax", "none", "strict") else "lax"


@router.post("/auth/login", response_model=User)
def login(body: LoginIn, request: Request, response: Response):
    peer = request.client.host if request.client else "unknown"
    hit("login_ip", peer, 30, 900)
    hit("login_email", (peer, body.email), 5, 900)
    with db.connect() as conn:
        user = conn.execute("SELECT id,email,name,role,routes,password_hash,active FROM users WHERE lower(email)=%s",
                            (body.email,)).fetchone()
    valid = verify_password(body.password, user["password_hash"] if user else DUMMY_HASH)
    if not valid or not user or not user["active"]:
        raise HTTPException(401, "Invalid email or password")
    samesite = cookie_samesite()
    response.set_cookie(COOKIE, sign_token(user), max_age=SESSION_SECONDS, httponly=True,
                        secure=samesite == "none" or request.url.scheme == "https" or env("APP_URL").startswith("https://"),
                        samesite=samesite, path="/")
    return {**user, "routes": list(ROUTES) if user["role"] == "admin" else user["routes"]}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    samesite = cookie_samesite()
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite=samesite,
                           secure=samesite == "none" or request.url.scheme == "https" or env("APP_URL").startswith("https://"))
    return {"ok": True}


@router.get("/auth/me", response_model=User)
def me(user=Depends(current_user)):
    return {**user, "routes": list(ROUTES) if user["role"] == "admin" else user["routes"]}


@router.get("/users", response_model=list[Account])
def users(admin=Depends(require_admin)):
    with db.connect() as conn:
        return conn.execute("SELECT id,email,name,role,routes,active,created_at FROM users ORDER BY id").fetchall()


@router.post("/users", response_model=Account, status_code=201)
def create_user(body: UserIn, admin=Depends(require_admin)):
    try:
        with db.connect() as conn:
            return conn.execute(
                "INSERT INTO users(email,name,password_hash,role,routes) VALUES (%s,%s,%s,%s,%s)"
                " RETURNING id,email,name,role,routes,active,created_at",
                (body.email, body.name, hash_password(body.password), body.role, body.routes)).fetchone()
    except UniqueViolation as exc:
        raise HTTPException(409, "Email already exists") from exc


@router.patch("/users/{user_id}", response_model=Account)
def update_routes(user_id: int, body: RoutesIn, admin=Depends(require_admin)):
    with db.connect() as conn:
        target = conn.execute("SELECT role FROM users WHERE id=%s FOR UPDATE", (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, "User not found")
        if target["role"] == "admin":
            raise HTTPException(400, "Cannot change an admin's routes")
        return conn.execute(
            "UPDATE users SET routes=%s WHERE id=%s RETURNING id,email,name,role,routes,active,created_at",
            (body.routes, user_id)).fetchone()


def seed_admin(conn):
    conn.execute("LOCK TABLE users IN EXCLUSIVE MODE")
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return
    email, password = env("ADMIN_EMAIL"), env("ADMIN_PASSWORD")
    if not email or not password:
        logging.getLogger(__name__).warning("Users table is empty; set ADMIN_EMAIL and ADMIN_PASSWORD to create the first admin")
        return
    admin = UserIn(email=email, password=password, name="Administrator", role="admin")
    conn.execute("INSERT INTO users(email,name,password_hash,role) VALUES (%s,%s,%s,'admin')",
                 (admin.email, admin.name, hash_password(admin.password)))
