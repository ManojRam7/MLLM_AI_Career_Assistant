"""Adzuna API (free) - aggregates Indeed, Totaljobs, CV-Library, Glassdoor and more.
https://developer.adzuna.com"""
from __future__ import annotations

import re

import requests

from ..models import Job
from .base import Source, SourceResult

# Adzuna aggregates many boards; board-reposts usually have no real employer. Keep DIRECT employers.
_VAGUE_CO = re.compile(r"^\s*(unspecified|confidential|competitive|various|not specified|n/?a|"
                       r"recruitment|company confidential|private advertiser|client)\s*$", re.I)
_BOARD_CO = re.compile(r"(cv[-\s]?library|totaljobs|reed\.co|jobsite|jobrapido|neuvoo|talent\.com|"
                       r"jobg8|adzuna|workingmums|whatjobs|jooble|careerjet|jobtoday|"
                       r"find a job|indeed|glassdoor)", re.I)


def _direct_employer(company: str) -> bool:
    """True only for a real, named direct employer (not blank / 'unspecified' / a job board)."""
    c = (company or "").strip()
    return bool(c) and not _VAGUE_CO.match(c) and not _BOARD_CO.search(c)


class AdzunaSource(Source):
    name = "Adzuna"

    def __init__(self, app_id: str, app_key: str, country: str = "gb", *,
                 countries: list[str] | None = None, primary: str = "gb",
                 max_queries_per_country: int = 5):
        self.app_id = app_id
        self.app_key = app_key
        # GLOBAL (v2): sweep a prioritised list of Adzuna country endpoints. The primary country (UK)
        # runs ALL queries; each other country runs only the first N core queries (credit control).
        self.countries = [c for c in (countries or [country or "gb"]) if c]
        self.primary = primary or (self.countries[0] if self.countries else "gb")
        self.max_qpc = max(1, max_queries_per_country)

    def _fetch_country(self, code: str, queries, per_query: int, recency_days: int) -> tuple[list[Job], int]:
        jobs: list[Job] = []
        dropped = 0
        for q in queries:
            url = f"https://api.adzuna.com/v1/api/jobs/{code}/search/1"
            params = {"app_id": self.app_id, "app_key": self.app_key, "what_phrase": q,
                      "what_exclude": "apprenticeship bootcamp",
                      "results_per_page": min(50, per_query), "max_days_old": recency_days,
                      "sort_by": "date"}
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            for it in r.json().get("results", []):
                company = (it.get("company") or {}).get("display_name", "")
                if not _direct_employer(company):
                    dropped += 1
                    continue
                loc = (it.get("location") or {}).get("display_name", "")
                jobs.append(Job(
                    title=it.get("title", ""), company=company, location=loc,
                    url=it.get("redirect_url", ""), description=it.get("description", ""),
                    posted_date=it.get("created", ""), salary=str(it.get("salary_min") or ""),
                    remote=("remote" in loc.lower()), source=self.name, source_query=q,
                ).finalize())
        return jobs, dropped

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        if not (self.app_id and self.app_key):
            return SourceResult(self.name, status="skipped", message="ADZUNA keys not set")
        per_query = max(10, limit // max(1, len(queries)))
        jobs: list[Job] = []
        dropped_board = 0
        per_country: list[str] = []
        try:
            for code in self.countries:
                qs = list(queries) if code == self.primary else list(queries)[:self.max_qpc]
                rpp = per_query if code == self.primary else min(20, per_query)
                got, dr = self._fetch_country(code, qs, rpp, recency_days)
                jobs.extend(got)
                dropped_board += dr
                per_country.append(f"{code}:{len(got)}")
        except requests.RequestException as exc:
            if not jobs:
                return SourceResult(self.name, jobs=jobs, status="error", message=str(exc))
            # partial success across countries — keep what we got
        cap = max(limit, 60 * len(self.countries))
        return SourceResult(self.name, jobs=jobs[:cap],
                            message=(f"{len(jobs)} direct-employer jobs across {len(self.countries)} "
                                     f"countries ({' · '.join(per_country)}) · {dropped_board} board-reposts dropped"))
