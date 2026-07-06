"""Google job discovery via the Bright Data SERP API.

Bright Data's brd_json=1 parses STANDARD Google search into `organic` results (it does NOT
structure the ibp=htl;jobs vertical widget). So we run standard searches that are targeted at
job boards + government portals so each organic result IS an individual job posting:

  <role> United Kingdom (site:uk.linkedin.com/jobs OR site:reed.co.uk OR site:totaljobs.com ...)
  site:civilservicejobs.service.gov.uk <role>        (gov)
  "<company>" <role> United Kingdom                  (rotating per-company for the active sector)

Cost: broad-query strategy keeps this to ~22 SERP calls/run; Bright Data bills per 1,000
successful requests (failures free). Fails gracefully - any error returns an error status."""
from __future__ import annotations

import html
import re
from urllib.parse import quote_plus

import requests

from ..models import Job
from .base import Source, SourceResult

ENDPOINT = "https://api.brightdata.com/request"
_TAG = re.compile(r"<[^>]+>")
# links that look like an individual posting (not a search/listing page)
_JOB_LINK = re.compile(r"(/jobs?/view|/job/|/jobs/\d|/vacancy|/vacancies/|viewjob|/job-detail|"
                       r"reed\.co\.uk/jobs/|totaljobs\.com/job/|cv-library\.co\.uk/job/|"
                       r"civilservicejobs\.service\.gov\.uk/csr|jobs\.nhs\.uk/candidate/jobadvert)", re.I)


def _clean(s: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", s or "")).split())


class BrightDataSerpSource(Source):
    name = "Google (Bright Data)"

    def __init__(self, api_key, zone="serp", *, bucket_path=None, sector=None, run_broad=True,
                 extra_queries=None, site_queries=None, search_domains=None,
                 top_companies_per_run=5, max_queries=22, pages=1, country="gb"):
        self.api_key = api_key
        self.zone = zone or "serp"
        self.bucket_path = bucket_path
        self.sector = sector
        self.run_broad = run_broad
        self.extra_queries = list(extra_queries or [])
        self.site_queries = list(site_queries or [])
        self.search_domains = list(search_domains or [
            "uk.linkedin.com/jobs", "www.reed.co.uk/jobs", "www.totaljobs.com",
            "www.cv-library.co.uk", "uk.indeed.com", "www.glassdoor.co.uk"])
        self.top_companies_per_run = top_companies_per_run
        self.max_queries = max_queries
        self.pages = max(1, pages)
        self.country = country

    def _board_filter(self) -> str:
        return "(" + " OR ".join(f"site:{d}" for d in self.search_domains) + ")"

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        if not self.api_key:
            return SourceResult(self.name, status="skipped", message="no BRIGHTDATA_API_KEY set")
        board = self._board_filter()
        plan: list[tuple[str, str]] = []                 # (query, company_hint)
        if self.run_broad:
            for q in self.extra_queries + list(queries[:2]):
                plan.append((f"{q} {board}", ""))        # broad role queries scoped to job boards
            for q in self.site_queries:                  # gov / LinkedIn site: queries (company known-ish)
                hint = "UK Civil Service" if "civilservicejobs" in q else ("NHS" if "jobs.nhs" in q else "")
                plan.append((q, hint))
        if self.bucket_path:
            from ..bucketlist import sample_top_companies
            for c in sample_top_companies(self.bucket_path, self.top_companies_per_run, self.sector):
                plan.append((f'"{c}" data scientist United Kingdom', c))
                plan.append((f'"{c}" data analyst United Kingdom', c))
        # de-dup queries, cap for cost
        seen_q, uniq_plan = set(), []
        for q, h in plan:
            if q in seen_q:
                continue
            seen_q.add(q)
            uniq_plan.append((q, h))
        uniq_plan = uniq_plan[:self.max_queries]
        if not uniq_plan:
            return SourceResult(self.name, status="skipped", message="no queries for this run")

        jobs: list[Job] = []
        errors = calls = 0
        for q, hint in uniq_plan:
            for page in range(self.pages):
                data = self._serp(q, start=page * 10)
                calls += 1
                if data is None:
                    errors += 1
                    break
                found = self._extract(data, q, hint)
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
                            message=f"{len(uniq)} jobs · {len(uniq_plan)} queries · {calls} calls · {errors} errors")

    def _serp(self, query: str, start: int = 0):
        url = f"https://www.google.com/search?q={quote_plus(query)}&brd_json=1&gl={self.country}&hl=en"
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
            return None
        except requests.RequestException:
            return None

    def _extract(self, data, query: str, company_hint: str) -> list[Job]:
        out: list[Job] = []
        if not isinstance(data, dict):
            return out
        for it in data.get("organic", []) or []:
            if not isinstance(it, dict):
                continue
            title, link = _clean(it.get("title", "")), it.get("link", "")
            if not title or not link or not _JOB_LINK.search(link):
                continue
            company = company_hint or self._company_from(title)
            out.append(Job(title=self._clean_title(title), company=company, location="United Kingdom",
                           url=link, description=_clean(it.get("description", "")),
                           source=self.name, source_query=query).finalize())
        return out

    @staticmethod
    def _company_from(title: str) -> str:
        m = re.match(r"(.+?)\s+hiring\s+", title)                 # LinkedIn: "Company hiring Title in Location"
        if m:
            return m.group(1).strip()
        m = re.search(r"\bat\s+([A-Z][\w&.,'\- ]{1,40})", title)   # "Title at Company"
        if m:
            return m.group(1).strip(" -|·")
        return ""

    @staticmethod
    def _clean_title(title: str) -> str:
        # strip board suffixes/prefixes so classify + dedup work on the real role
        t = re.sub(r"\s*[|\-–]\s*(LinkedIn|Reed\.co\.uk|Totaljobs|CV-Library|Indeed|Glassdoor|jobs\.nhs\.uk).*$",
                   "", title, flags=re.I)
        t = re.sub(r"^.+?\s+hiring\s+", "", t, flags=re.I)          # drop "Company hiring "
        t = re.sub(r"\s+in\s+[A-Z][\w ,]+$", "", t)                 # drop trailing " in Location"
        return t.strip() or title
