"""Load settings.yaml, .env secrets and the base CV."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """A required secret or setting is missing or malformed (shown to the user, no traceback)."""


def _resolve(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


@dataclass
class Secrets:
    reed_api_key: str = ""
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    supabase_db_url: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    deepseek_api_key: str = ""
    apify_token: str = ""
    apify_tokens: list[str] = field(default_factory=list)
    serpapi_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def from_env(cls) -> "Secrets":
        g = os.environ.get
        return cls(
            reed_api_key=g("REED_API_KEY", ""),
            adzuna_app_id=g("ADZUNA_APP_ID", ""),
            adzuna_app_key=g("ADZUNA_APP_KEY", ""),
            supabase_db_url=g("SUPABASE_DB_URL", ""),
            gemini_api_key=g("GEMINI_API_KEY", ""),
            groq_api_key=g("GROQ_API_KEY", ""),
            deepseek_api_key=g("DEEPSEEK_API_KEY", ""),
            apify_token=g("APIFY_TOKEN", ""),
            apify_tokens=[t for t in (g("APIFY_TOKEN_1", ""), g("APIFY_TOKEN_2", ""),
                                      g("APIFY_TOKEN_3", ""), g("APIFY_TOKEN_4", ""),
                                      g("APIFY_TOKEN", "")) if t],
            serpapi_key=g("SERPAPI_KEY", ""),
            telegram_bot_token=g("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=g("TELEGRAM_CHAT_ID", ""),
        )


@dataclass
class Config:
    settings: dict[str, Any]
    secrets: Secrets
    base_cv: dict[str, Any]

    @property
    def root(self) -> Path:
        return ROOT

    def path(self, rel: str) -> Path:
        return _resolve(rel)


def load_config(settings_path: str = "config/settings.yaml") -> Config:
    settings = yaml.safe_load(_resolve(settings_path).read_text(encoding="utf-8")) or {}
    base_cv_rel = settings.get("candidate", {}).get("base_cv", "src/uk_jobops/cv/base_cv.json")
    base_cv = json.loads(_resolve(base_cv_rel).read_text(encoding="utf-8"))
    return Config(settings=settings, secrets=Secrets.from_env(), base_cv=base_cv)
