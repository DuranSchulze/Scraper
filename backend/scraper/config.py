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


def env_list(name: str) -> list[str]:
    return [x.strip().lower() for x in env(name).split(",") if x.strip()]


def load_rules() -> dict:
    return yaml.safe_load(RULES_PATH.read_text())
