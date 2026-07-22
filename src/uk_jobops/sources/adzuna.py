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

    def __init__(self, app_id: str, app_key: str, country: str = "gb"):
        self.app_id = app_id
        self.app_key = app_key
        self.country = country

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        if not (self.app_id and self.app_key):
            return SourceResult(self.name, status="skipped", message="ADZUNA keys not set")
        jobs: list[Job] = []
        per_query = max(10, limit // max(1, len(queries)))
        dropped_board = 0
        try:
            for q in queries:
                url = f"https://api.adzuna.com/v1/api/jobs/{self.country}/search/1"
                params = {
                    "app_id": self.app_id,
                    "app_key": self.app_key,
                    "what_phrase": q,          # tighter: match the phrase, not any-of-the-words
                    "what_exclude": "apprenticeship bootcamp",  # pure noise only
                    # (seniority is filtered on the TITLE later, not here, to avoid dropping juniors
                    #  whose description merely mentions a "senior" colleague)
                    # country is already in the URL path (/gb/); a 'where' of
                    # "United Kingdom" matches no location and returns 0, so omit it.
                    "results_per_page": min(50, per_query),
                    "max_days_old": recency_days,
                    "sort_by": "date",
                }
                r = requests.get(url, params=params, timeout=30)
                r.raise_for_status()
                for it in r.json().get("results", []):
                    company = (it.get("company") or {}).get("display_name", "")
                    if not _direct_employer(company):     # drop board-reposts / vague employers
                        dropped_board += 1
                        continue
                    jobs.append(Job(
                        title=it.get("title", ""),
                        company=company,
                        location=(it.get("location") or {}).get("display_name", ""),
                        url=it.get("redirect_url", ""),
                        description=it.get("description", ""),
                        posted_date=it.get("created", ""),
                        salary=str(it.get("salary_min") or ""),
                        remote=bool(it.get("location", {}).get("display_name", "").lower().find("remote") >= 0),
                        source=self.name,
                        source_query=q,
                    ).finalize())
                if len(jobs) >= limit:
                    break
        except requests.RequestException as exc:
            return SourceResult(self.name, jobs=jobs, status="error", message=str(exc))
        return SourceResult(self.name, jobs=jobs[:limit],
                            message=f"{len(jobs)} direct-employer jobs · {dropped_board} board-reposts dropped")
