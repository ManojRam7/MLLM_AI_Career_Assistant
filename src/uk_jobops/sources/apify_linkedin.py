"""Structured LinkedIn Jobs via a cheap Apify actor (default valig/linkedin-jobs-scraper),
filtered by companyName so we search EACH of your list companies on LinkedIn - the per-company
coverage the SERP path can't guarantee. Returns structured company/location/date, UK-filtered,
past-week only. ~$0.0004/result (vs Bright Data's $250 min dataset). Adapted from the user's own
job_scraper design. Needs APIFY_TOKEN(s). Fails gracefully."""
from __future__ import annotations

import requests

from ..models import Job
from .base import Source, SourceResult
from .brightdata_serp import looks_non_uk

ENDPOINT = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return ""


class ApifyLinkedInSource(Source):
    name = "LinkedIn (Apify)"

    def __init__(self, tokens, companies, *, actor="valig~linkedin-jobs-scraper", title_queries=None,
                 location="United Kingdom", date_posted="r604800", batch_size=40,
                 max_jobs_per_company=6, max_batches=8):
        self.tokens = [t for t in (tokens or []) if t]
        self.companies = companies or []
        self.actor = actor.replace("/", "~")
        self.title_queries = title_queries or [
            "data scientist OR machine learning engineer OR AI engineer",
            "data analyst OR analytics engineer OR business intelligence analyst"]
        self.location = location
        self.date_posted = date_posted            # r604800 = past 7 days
        self.batch_size = batch_size
        self.max_jobs_per_company = max_jobs_per_company
        self.max_batches = max_batches

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        if not self.tokens:
            return SourceResult(self.name, status="skipped", message="no APIFY_TOKEN set")
        names = [(c[0] if isinstance(c, (tuple, list)) else c) for c in self.companies]
        names = [n for n in names if n]
        if not names:
            return SourceResult(self.name, status="skipped", message="no companies for this run")
        batches = [names[i:i + self.batch_size] for i in range(0, len(names), self.batch_size)][:self.max_batches]
        jobs: list[Job] = []
        runs = errors = 0
        for batch in batches:
            for q in self.title_queries:
                items = self._run({
                    "title": q, "location": self.location, "datePosted": self.date_posted,
                    "companyName": batch, "contractType": ["F"],
                    "experienceLevel": ["1", "2", "3", "4"], "remote": ["1", "2", "3"],
                    "limit": len(batch) * self.max_jobs_per_company})
                runs += 1
                if items is None:
                    errors += 1
                    continue
                jobs.extend(self._to_jobs(items, q))
            if len(jobs) >= limit:
                break
        seen, uniq = set(), []
        for j in jobs:
            if not j.url or j.url in seen:
                continue
            seen.add(j.url)
            uniq.append(j)
        status = "ok" if (uniq or not errors) else "error"
        return SourceResult(self.name, jobs=uniq[:limit], status=status,
                            message=f"{len(uniq)} UK LinkedIn jobs · {runs} actor runs · {errors} errors")

    def _run(self, inp):
        url = ENDPOINT.format(actor=self.actor)
        for token in self.tokens:
            try:
                r = requests.post(url, params={"token": token}, json=inp, timeout=180)
                if r.status_code in (200, 201):
                    data = r.json()
                    return data if isinstance(data, list) else []
                # 401/402/403/429 -> token spent/unauthorised, try the next
            except requests.RequestException:
                continue
        return None

    def _to_jobs(self, items, query) -> list[Job]:
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            title = _first(it, "title", "jobTitle", "job_title")
            company = _first(it, "companyName", "company", "company_name")
            loc = _first(it, "location", "jobLocation")
            url = _first(it, "jobUrl", "url", "link", "job_url")
            desc = str(_first(it, "description", "descriptionText", "job_description"))[:2500]
            posted = str(_first(it, "postedAt", "publishedAt", "postedDate", "posted_time"))[:10]
            if not title or not url:
                continue
            if looks_non_uk(f"{loc} {title}"):
                continue
            out.append(Job(title=title, company=company, location=loc or "United Kingdom", url=url,
                           description=desc, posted_date=posted, source=self.name,
                           source_query=query).finalize())
        return out
