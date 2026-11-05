"""Settings from .env and extraction rules from rules.yaml."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "rules.yaml"

load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_list(name: str) -> list[str]:
    return [x.strip().lower() for x in env(name).split(",") if x.strip()]


def load_rules() -> dict:
    return yaml.safe_load(RULES_PATH.read_text())
