"""Core data models shared across the pipeline."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


@dataclass
class Job:
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    description: str = ""
    posted_date: str = ""
    salary: str = ""
    remote: bool = False
    source: str = ""
    source_query: str = ""
    # enrichment / pipeline fields
    dedupe_key: str = ""
    first_seen_at: str = ""
    fetched_at: str = ""
    seniority: str = ""
    category: str = ""           # "data-science" | "data-analysis" | "" (derived from title)
    sector: str = ""             # Master-List sector of the employer (derived from company)
    is_target: bool = True
    fit_score: int = 0
    fit_reasoning: str = ""
    ghost_flag: bool = False
    status: str = "new"          # new | scored | shortlisted | tailored | applied | interview | offer | rejected
    is_custom: bool = False      # manually added by the user
    in_bucket: bool = False      # company is on the bucket list (gets priority)
    bucket_tier: str = ""        # "top100" | "master" | "" (top100 = highest priority)
    notes: str = ""              # free-text tracker notes
    locations: str = ""          # every location this same role was seen in (aggregated)
    cv_path: str = ""
    cover_path: str = ""
    gaps: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def make_key(self) -> str:
        # identity = the posting URL. Every distinct posting has a unique URL, so we
        # never merge two different roles. Query params are stripped so the same posting
        # is stable across runs. Falls back to title+company+location when there's no URL.
        u = (self.url or "").split("?")[0].rstrip("/").strip().lower()
        base = u or "|".join([_norm(self.title), _norm(self.company), _norm(self.location)])
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]

    def finalize(self) -> "Job":
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self.fetched_at = self.fetched_at or now
        self.first_seen_at = self.first_seen_at or now
        self.locations = self.locations or self.location
        self.dedupe_key = self.dedupe_key or self.make_key()
        return self

    def to_db(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        d["gaps"] = list(self.gaps)
        return d


@dataclass
class FitResult:
    score: int = 0
    band: str = ""              # A-F or High/Med/Low
    reasoning: str = ""
    ghost_flag: bool = False
    gaps: list[str] = field(default_factory=list)


@dataclass
class TailoredCV:
    job_title_line: str = ""
    profile: str = ""
    skills: list[dict[str, str]] = field(default_factory=list)
    experience: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, str]] = field(default_factory=list)
    education: str = ""
    cover_letter: list[str] = field(default_factory=list)
    jd_keywords: list[str] = field(default_factory=list)
    keyword_coverage: float = 0.0
    gaps: list[str] = field(default_factory=list)
    fit_score: int = 0
    fit_reasoning: str = ""
