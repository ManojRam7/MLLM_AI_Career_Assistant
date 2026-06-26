"""Clean and standardise raw jobs before storage."""
from __future__ import annotations

import re

from .models import Job

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(html: str, limit: int = 6000) -> str:
    text = _WS.sub(" ", _TAG.sub(" ", html or "")).strip()
    return text[:limit]


def iso_date(value: str) -> str:
    if not value:
        return ""
    try:
        from dateutil import parser

        return parser.parse(value).date().isoformat()
    except Exception:
        return str(value)[:10]


def normalize(jobs: list[Job]) -> list[Job]:
    for j in jobs:
        j.title = _WS.sub(" ", (j.title or "").strip())
        j.company = _WS.sub(" ", (j.company or "").strip())
        j.location = _WS.sub(" ", (j.location or "").strip()) or "United Kingdom"
        j.description = clean_text(j.description)
        j.posted_date = iso_date(j.posted_date)
        if not j.remote and "remote" in (j.location + " " + j.title).lower():
            j.remote = True
        j.finalize()
    return jobs
