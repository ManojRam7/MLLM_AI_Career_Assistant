"""Company ATS APIs (free, accurate) for your bucket-list companies.
Detects Greenhouse / Lever / Ashby from each careers URL and pulls live postings.
Bucket-list CSV columns: company_name, careers_url"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import requests

from ..models import Job
from .base import Source, SourceResult


def detect_ats(url: str) -> tuple[str, str] | None:
    """Return (ats, board_token) from a careers URL, or None."""
    u = url.lower()
    for pat, ats in [
        (r"boards\.greenhouse\.io/([a-z0-9_-]+)", "greenhouse"),
        (r"job-boards\.greenhouse\.io/([a-z0-9_-]+)", "greenhouse"),
        (r"jobs\.lever\.co/([a-z0-9_-]+)", "lever"),
        (r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", "ashby"),
    ]:
        m = re.search(pat, u)
        if m:
            return ats, m.group(1)
    return None


class ATSSource(Source):
    name = "Company ATS"

    def __init__(self, bucket_list_path: str, include_terms: list[str]):
        self.path = Path(bucket_list_path)
        self.include = [t.lower() for t in include_terms]

    def _companies(self) -> list[tuple[str, str]]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("company_name") or row.get("Company Name") or "").strip()
                url = (row.get("careers_url") or row.get("Careers Page") or "").strip()
                if name and url:
                    out.append((name, url))
        return out

    def _matches(self, title: str) -> bool:
        t = title.lower()
        return any(term in t for term in self.include)

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        companies = self._companies()
        if not companies:
            return SourceResult(self.name, status="skipped", message=f"No bucket list at {self.path}")
        jobs: list[Job] = []
        errors = 0
        for company, url in companies:
            if len(jobs) >= limit:
                break
            detected = detect_ats(url)
            if not detected:
                continue
            ats, token = detected
            try:
                jobs.extend(self._pull(ats, token, company))
            except requests.RequestException:
                errors += 1
        jobs = [j for j in jobs if self._matches(j.title)]
        return SourceResult(self.name, jobs=jobs[:limit],
                            message=f"{len(companies)} companies, {len(jobs)} matched DS roles, {errors} errors")

    def _pull(self, ats: str, token: str, company: str) -> list[Job]:
        out: list[Job] = []
        if ats == "greenhouse":
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true", timeout=30)
            r.raise_for_status()
            for it in r.json().get("jobs", []):
                out.append(Job(title=it.get("title", ""), company=company,
                               location=(it.get("location") or {}).get("name", ""),
                               url=it.get("absolute_url", ""), description=it.get("content", ""),
                               posted_date=it.get("updated_at", ""), source=self.name).finalize())
        elif ats == "lever":
            r = requests.get(f"https://api.lever.co/v0/postings/{token}?mode=json", timeout=30)
            r.raise_for_status()
            for it in r.json():
                cats = it.get("categories", {})
                out.append(Job(title=it.get("text", ""), company=company,
                               location=cats.get("location", ""), url=it.get("hostedUrl", ""),
                               description=it.get("descriptionPlain", ""),
                               posted_date=str(it.get("createdAt", "")), source=self.name).finalize())
        elif ats == "ashby":
            r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", timeout=30)
            r.raise_for_status()
            for it in r.json().get("jobs", []):
                out.append(Job(title=it.get("title", ""), company=company,
                               location=it.get("locationName", ""), url=it.get("jobUrl", ""),
                               description=it.get("descriptionPlain", ""), source=self.name).finalize())
        return out
