"""Structured LinkedIn Jobs via the Bright Data Web Scraper API (dataset).

Unlike SERP-on-LinkedIn (which guesses company/location/expiry from a short snippet), this returns
STRUCTURED fields - real company, real location, posted date, and active/expired status - so
expired and non-UK jobs are excluded RELIABLY. Discovery by keyword (data scientist / data analyst,
past month, UK). Async: trigger -> poll -> download. Fails gracefully (any error -> error status).

Setup: create the 'LinkedIn job listings' scraper in the Bright Data Scraper Library, copy its
dataset_id, and set BRIGHTDATA_LINKEDIN_DATASET (secret) + sources.linkedin.enabled: true.
Billing: per delivered record (~$0.7-2/1000); 'Past month' + keyword discovery keeps volume low."""
from __future__ import annotations

import datetime as dt
import time

import requests

from ..models import Job
from .base import Source, SourceResult
from .brightdata_serp import looks_non_uk

TRIGGER = "https://api.brightdata.com/datasets/v3/trigger"
PROGRESS = "https://api.brightdata.com/datasets/v3/progress/{}"
SNAPSHOT = "https://api.brightdata.com/datasets/v3/snapshot/{}"


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return ""


class BrightDataLinkedInSource(Source):
    name = "LinkedIn (Bright Data)"

    def __init__(self, api_key, dataset_id, *, keywords=None, location="United Kingdom",
                 country="GB", time_range="Past month", max_wait=480, poll=15, max_age_days=30):
        self.api_key = api_key
        self.dataset_id = dataset_id
        self.keywords = keywords or ["data scientist", "data analyst"]
        self.location = location
        self.country = country
        self.time_range = time_range
        self.max_wait = max_wait
        self.poll = poll
        self.max_age_days = max_age_days

    def _h(self):
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        if not (self.api_key and self.dataset_id):
            return SourceResult(self.name, status="skipped",
                                message="no BRIGHTDATA_API_KEY / LinkedIn dataset_id set")
        payload = [{"keyword": k, "location": self.location, "country": self.country,
                    "time_range": self.time_range} for k in self.keywords]
        try:
            r = requests.post(TRIGGER, headers=self._h(), json=payload, timeout=60,
                              params={"dataset_id": self.dataset_id, "type": "discover_new",
                                      "discover_by": "keyword", "format": "json", "limit_per_input": 50})
            if r.status_code not in (200, 202):
                return SourceResult(self.name, status="error", message=f"trigger HTTP {r.status_code}: {r.text[:90]}")
            snap = r.json().get("snapshot_id")
            if not snap:
                return SourceResult(self.name, status="error", message="no snapshot_id returned")
        except requests.RequestException as exc:
            return SourceResult(self.name, status="error", message=f"trigger error: {str(exc)[:90]}")

        waited = 0
        while waited < self.max_wait:
            try:
                status = requests.get(PROGRESS.format(snap), headers=self._h(), timeout=30).json().get("status")
            except requests.RequestException:
                status = None
            if status == "ready":
                break
            if status == "failed":
                return SourceResult(self.name, status="error", message="scrape job failed")
            time.sleep(self.poll)
            waited += self.poll
        else:
            return SourceResult(self.name, status="error",
                                message=f"timeout after {self.max_wait}s (snapshot {snap})")

        try:
            data = requests.get(SNAPSHOT.format(snap), headers=self._h(),
                                params={"format": "json"}, timeout=180).json()
        except Exception as exc:  # noqa: BLE001
            return SourceResult(self.name, status="error", message=f"download error: {str(exc)[:90]}")

        jobs = self._parse(data)
        return SourceResult(self.name, jobs=jobs[:limit],
                            message=f"{len(jobs)} active UK LinkedIn jobs from {len(self.keywords)} keywords")

    def _parse(self, data) -> list[Job]:
        rows = data if isinstance(data, list) else (data.get("data", []) if isinstance(data, dict) else [])
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=self.max_age_days)
        out: list[Job] = []
        for it in rows:
            if not isinstance(it, dict):
                continue
            title = _first(it, "job_title", "title")
            company = _first(it, "company_name", "company", "employer")
            loc = _first(it, "job_location", "location")
            url = _first(it, "url", "job_url", "link", "apply_link")
            desc = str(_first(it, "job_summary", "job_description", "description"))[:2500]
            posted = str(_first(it, "job_posted_date", "posted_date", "job_posted_time", "date_posted"))[:10]
            status = str(_first(it, "application_availability", "job_posting_status", "is_active")).lower()
            if not title or not url:
                continue
            if looks_non_uk(f"{loc} {title} {desc}"):                  # UK only (real location)
                continue
            if status in ("false", "closed", "expired", "inactive") or "no longer" in status:
                continue                                               # active only
            out.append(Job(title=title, company=company, location=loc or "United Kingdom", url=url,
                           description=desc, posted_date=posted, source=self.name).finalize())
        return out
