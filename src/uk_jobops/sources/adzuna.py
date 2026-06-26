"""Adzuna API (free) - aggregates Indeed, Totaljobs, CV-Library, Glassdoor and more.
https://developer.adzuna.com"""
from __future__ import annotations

import requests

from ..models import Job
from .base import Source, SourceResult


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
        try:
            for q in queries:
                url = f"https://api.adzuna.com/v1/api/jobs/{self.country}/search/1"
                params = {
                    "app_id": self.app_id,
                    "app_key": self.app_key,
                    "what": q,
                    # country is already in the URL path (/gb/); a 'where' of
                    # "United Kingdom" matches no location and returns 0, so omit it.
                    "results_per_page": min(50, per_query),
                    "max_days_old": recency_days,
                    "sort_by": "date",
                }
                r = requests.get(url, params=params, timeout=30)
                r.raise_for_status()
                for it in r.json().get("results", []):
                    jobs.append(Job(
                        title=it.get("title", ""),
                        company=(it.get("company") or {}).get("display_name", ""),
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
        return SourceResult(self.name, jobs=jobs[:limit], message=f"{len(jobs)} fetched")
