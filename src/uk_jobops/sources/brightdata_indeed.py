"""Structured Indeed Jobs via the Bright Data Web Scraper API (dataset).

Like the structured LinkedIn source, this returns STRUCTURED fields (real company, location,
posted date, active/expired) rather than guessing from a SERP snippet - so non-UK and expired
Indeed jobs are excluded reliably. Discovery by keyword on indeed.co.uk. Async: trigger -> poll ->
download. Fails gracefully (any error -> error status, pipeline continues).

Setup: create the 'Indeed job listings information - discover by keyword' scraper in the Bright Data
Scraper Library, copy its dataset_id, set BRIGHTDATA_INDEED_DATASET (secret) + sources.indeed.enabled.
NOTE: the exact trigger input field names + discover_by can vary by dataset version; the first live
run's error message (surfaced in the run log) tells us if a field name needs a small tweak."""
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


class BrightDataIndeedSource(Source):
    name = "Indeed (Bright Data)"

    def __init__(self, api_key, dataset_id, *, keywords=None, location="United Kingdom",
                 country="GB", domain="indeed.co.uk", date_posted="Last 7 days",
                 max_wait=480, poll=15, max_age_days=30):
        self.api_key = api_key
        self.dataset_id = dataset_id
        self.keywords = keywords or ["data scientist", "data analyst"]
        self.location = location
        self.country = country
        self.domain = domain
        self.date_posted = date_posted
        self.max_wait = max_wait
        self.poll = poll
        self.max_age_days = max_age_days

    def _h(self):
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        if not (self.api_key and self.dataset_id):
            return SourceResult(self.name, status="skipped",
                                message="no BRIGHTDATA_API_KEY / Indeed dataset_id set")
        # discover-by-keyword input for the Indeed dataset. Keep to the CORE fields the dataset
        # validates - extra fields (domain / date_posted enum) trigger 'Invalid input provided'.
        payload = [{"keyword_search": k, "location": self.location, "country": self.country}
                   for k in self.keywords]
        try:
            r = requests.post(TRIGGER, headers=self._h(), json=payload, timeout=60,
                              params={"dataset_id": self.dataset_id, "type": "discover_new",
                                      "discover_by": "keyword", "format": "json", "limit_per_input": 50})
            if r.status_code not in (200, 202):
                return SourceResult(self.name, status="error", message=f"trigger HTTP {r.status_code}: {r.text[:300]}")
            try:
                snap = r.json().get("snapshot_id")
            except Exception:
                return SourceResult(self.name, status="error", message=f"trigger non-JSON: {r.text[:300]}")
            if not snap:
                return SourceResult(self.name, status="error", message=f"no snapshot_id: {r.text[:250]}")
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
                            message=f"{len(jobs)} active UK Indeed jobs from {len(self.keywords)} keywords")

    def _parse(self, data) -> list[Job]:
        rows = data if isinstance(data, list) else (data.get("data", []) if isinstance(data, dict) else [])
        out: list[Job] = []
        for it in rows:
            if not isinstance(it, dict):
                continue
            title = _first(it, "job_title", "title", "jobtitle", "position", "name")
            company = _first(it, "company_name", "company", "employer", "companyname", "company_name_normalized")
            loc = _first(it, "location", "job_location", "formatted_location", "city", "jobLocation")
            url = _first(it, "url", "job_link", "apply_link", "link", "job_url", "joburl", "indeed_url", "apply_url")
            desc = str(_first(it, "description_text", "job_description", "description", "job_summary", "snippet"))[:2500]
            posted = str(_first(it, "date_posted", "posted_date", "job_posted_date", "date", "posted"))[:10]
            status = str(_first(it, "is_expired", "is_active", "job_status", "status")).lower()
            if not title or not url:
                continue
            if looks_non_uk(f"{loc} {title} {desc}"):
                continue
            if status in ("true", "expired", "closed", "inactive") and _first(it, "is_expired"):
                continue                                                # is_expired == true -> skip
            out.append(Job(title=title, company=company, location=loc or "United Kingdom", url=url,
                           description=desc, posted_date=posted, source=self.name).finalize())
        return out
