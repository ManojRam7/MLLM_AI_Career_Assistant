"""Reed.co.uk official API (free). https://www.reed.co.uk/developers"""
from __future__ import annotations

import requests

from ..models import Job
from .base import Source, SourceResult

BASE = "https://www.reed.co.uk/api/1.0/search"


class ReedSource(Source):
    name = "Reed"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        if not self.api_key:
            return SourceResult(self.name, status="skipped", message="REED_API_KEY not set")
        jobs: list[Job] = []
        per_query = max(10, limit // max(1, len(queries)))
        try:
            for q in queries:
                params = {
                    "keywords": q,
                    "locationName": "United Kingdom",
                    "resultsToTake": min(100, per_query),
                    "postedByDirectEmployer": "true",   # direct employers, not recruiters
                }
                r = requests.get(BASE, params=params, auth=(self.api_key, ""), timeout=30)
                r.raise_for_status()
                for it in r.json().get("results", []):
                    jobs.append(Job(
                        title=it.get("jobTitle", ""),
                        company=it.get("employerName", ""),
                        location=it.get("locationName", ""),
                        url=it.get("jobUrl", ""),
                        description=it.get("jobDescription", ""),
                        posted_date=it.get("date", ""),
                        salary=str(it.get("minimumSalary") or ""),
                        source=self.name,
                        source_query=q,
                    ).finalize())
                if len(jobs) >= limit:
                    break
        except requests.RequestException as exc:
            return SourceResult(self.name, jobs=jobs, status="error", message=str(exc))
        return SourceResult(self.name, jobs=jobs[:limit], message=f"{len(jobs)} fetched")
