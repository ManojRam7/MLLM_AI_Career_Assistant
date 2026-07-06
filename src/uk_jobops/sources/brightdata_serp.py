"""Google-for-Jobs discovery via the Bright Data SERP API (replaces the Apify actor).

Cost-optimised BROAD-QUERY strategy: a handful of market-wide queries aggregate the whole
UK data-job market (Google for Jobs indexes LinkedIn/Indeed/Glassdoor/company boards), plus
site-restricted queries for the government portals (civilservicejobs, jobs.nhs.uk) and
LinkedIn, plus a small rotating set of per-company queries for the active sector. Uses the
parsed `brd_json=1` output and fails gracefully (any error -> error status, pipeline continues).

Billing note: Bright Data bills per 1,000 successful SERP requests; failed requests are free.
At ~30-40 broad queries/day this stays inside the free-credit allotment for months."""
from __future__ import annotations

import html
import re
from urllib.parse import quote_plus

import requests

from ..models import Job
from .base import Source, SourceResult

ENDPOINT = "https://api.brightdata.com/request"
_TAG = re.compile(r"<[^>]+>")


def _clean(s: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", s or "")).split())


class BrightDataSerpSource(Source):
    name = "Google (Bright Data)"

    def __init__(self, api_key, zone="serp", *, bucket_path=None, sector=None, run_broad=True,
                 extra_queries=None, site_queries=None, top_companies_per_run=5, max_queries=20,
                 pages=1, country="gb"):
        self.api_key = api_key
        self.zone = zone or "serp"
        self.bucket_path = bucket_path
        self.sector = sector
        self.run_broad = run_broad
        self.extra_queries = list(extra_queries or [])
        self.site_queries = list(site_queries or [])
        self.top_companies_per_run = top_companies_per_run
        self.max_queries = max_queries
        self.pages = max(1, pages)
        self.country = country

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        if not self.api_key:
            return SourceResult(self.name, status="skipped", message="no BRIGHTDATA_API_KEY set")
        q_list: list[str] = []
        if self.run_broad:                                    # market-wide + gov/LinkedIn site: queries
            q_list += self.extra_queries + self.site_queries + list(queries[:2])
        if self.bucket_path:                                  # rotating per-company (this sector), both categories
            from ..bucketlist import sample_top_companies
            for c in sample_top_companies(self.bucket_path, self.top_companies_per_run, self.sector):
                q_list.append(f'"{c}" data scientist United Kingdom')
                q_list.append(f'"{c}" data analyst United Kingdom')
        q_list = list(dict.fromkeys(q_list))[:self.max_queries]
        if not q_list:
            return SourceResult(self.name, status="skipped", message="no queries for this run")

        jobs: list[Job] = []
        errors = calls = 0
        for q in q_list:
            for page in range(self.pages):
                data = self._serp(q, start=page * 10)
                calls += 1
                if data is None:
                    errors += 1
                    break
                found = self._extract(data, q)
                if not found:
                    break
                jobs.extend(found)
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
                            message=f"{len(uniq)} jobs from {len(q_list)} queries, {calls} SERP calls, {errors} errors")

    def _serp(self, query: str, start: int = 0):
        """One SERP call (Google for Jobs vertical + parsed JSON). None on failure."""
        url = (f"https://www.google.com/search?q={quote_plus(query)}"
               f"&ibp=htl;jobs&brd_json=1&gl={self.country}&hl=en")
        if start:
            url += f"&start={start}"
        try:
            r = requests.post(ENDPOINT,
                              headers={"Authorization": f"Bearer {self.api_key}",
                                       "Content-Type": "application/json"},
                              json={"zone": self.zone, "url": url, "format": "raw"}, timeout=90)
            if r.status_code in (200, 201):
                try:
                    return r.json()
                except ValueError:
                    return {}
            return None                                       # 4xx/5xx -> not billed, try next query
        except requests.RequestException:
            return None

    def _extract(self, data, query: str) -> list[Job]:
        out: list[Job] = []
        if not isinstance(data, dict):
            return out
        # 1) Google Jobs widget (richest: title/company/location) - try a few key names
        for key in ("jobs", "jobs_results", "job_results"):
            arr = data.get(key)
            if isinstance(arr, list) and arr:
                for it in arr:
                    if not isinstance(it, dict):
                        continue
                    url = (it.get("link") or it.get("apply_link") or it.get("job_link")
                           or it.get("share_link") or "")
                    if not url and isinstance(it.get("apply_options"), list) and it["apply_options"]:
                        first = it["apply_options"][0]
                        url = (first.get("link") if isinstance(first, dict) else "") or ""
                    out.append(Job(title=it.get("title", "") or "",
                                   company=it.get("company_name") or it.get("company") or "",
                                   location=it.get("location", "") or "United Kingdom", url=url,
                                   description=it.get("description", "") or "",
                                   source=self.name, source_query=query).finalize())
                if out:
                    return out
        # 2) fallback: organic results (individual job-posting pages)
        for it in data.get("organic", []) or []:
            if not isinstance(it, dict):
                continue
            title, link = _clean(it.get("title", "")), it.get("link", "")
            if not title or not link:
                continue
            out.append(Job(title=title, company=self._company_from(title),
                           location="United Kingdom", url=link,
                           description=_clean(it.get("description", "")),
                           source=self.name, source_query=query).finalize())
        return out

    @staticmethod
    def _company_from(title: str) -> str:
        m = re.match(r"(.+?)\s+hiring\s+", title)              # LinkedIn: "Company hiring Title in Location"
        if m:
            return m.group(1).strip()
        m = re.search(r"\bat\s+([A-Z][\w&.,'\- ]{1,40})", title)  # "Title at Company"
        if m:
            return m.group(1).strip(" -|")
        return ""
