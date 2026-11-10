"""Settings from .env and extraction rules from rules.yaml."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent  # backend/
RULES_PATH = ROOT / "rules.yaml"

# One .env at the repository root, shared by backend/ and frontend/ commands.
load_dotenv(ROOT.parent / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int, minimum: int, maximum: int, log=None) -> int:
    raw = env(name)
    try:
        requested = int(raw)
    except ValueError:
        requested = None
    value = default if requested is None else max(minimum, min(maximum, requested))
    if log and (requested is None or requested != value):
        log(f"{name}: using {value} (configured {raw!r})")
    return value


def env_list(name: str) -> list[str]:
    return [x.strip().lower() for x in env(name).split(",") if x.strip()]


def jwt_secret() -> str:
    secret = env("JWT_SECRET")
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 characters")
    return secret


def load_rules() -> dict:
    return yaml.safe_load(RULES_PATH.read_text())
