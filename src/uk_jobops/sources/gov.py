"""Government job-portal sources: NHS Jobs and Civil Service Jobs.

These public portals list many data-analyst / data-scientist roles (civil service
especially) that don't always surface on Reed/Adzuna. Both are server-rendered HTML,
parsed best-effort with regex. ANY failure returns an error status and the pipeline
continues with the other sources. Selectors may need a one-off tune after the first
live run - until then the Apify/Google 'data analyst civil service / NHS' queries also
cover these portals, so coverage never depends solely on these parsers."""
from __future__ import annotations

import html
import re

import requests

from ..models import Job
from .base import Source, SourceResult

_UA = {"User-Agent": "Mozilla/5.0 (compatible; jobops/1.0; +https://github.com/ManojRam7)"}
_TAG = re.compile(r"<[^>]+>")


def _clean(s: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", s or "")).split())


def _dedupe(jobs: list[Job]) -> list[Job]:
    seen, out = set(), []
    for j in jobs:
        if not j.url or j.url in seen:
            continue
        seen.add(j.url)
        out.append(j)
    return out


class NHSJobsSource(Source):
    """jobs.nhs.uk candidate search (server-rendered results)."""
    name = "NHS Jobs"
    BASE = "https://www.jobs.nhs.uk/candidate/search/results"

    def __init__(self, queries=None, max_pages=2):
        self.queries = queries or ["data analyst", "data scientist"]
        self.max_pages = max_pages

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        jobs: list[Job] = []
        errors = 0
        for kw in self.queries:
            for page in range(1, self.max_pages + 1):
                try:
                    r = requests.get(self.BASE, params={"keyword": kw, "page": page},
                                     timeout=30, headers=_UA)
                except requests.RequestException:
                    errors += 1
                    break
                if r.status_code != 200:
                    errors += 1
                    break
                found = self._parse(r.text, kw)
                if not found:
                    break
                jobs.extend(found)
                if len(jobs) >= limit:
                    break
            if len(jobs) >= limit:
                break
        uniq = _dedupe(jobs)
        status = "ok" if (uniq or not errors) else "error"
        return SourceResult(self.name, jobs=uniq[:limit], status=status,
                            message=f"{len(uniq)} roles from {len(self.queries)} queries, {errors} errors")

    def _parse(self, page_html: str, kw: str) -> list[Job]:
        out: list[Job] = []
        for m in re.finditer(r'href="(/candidate/jobadvert/[^"?]+)"[^>]*>(.*?)</a>', page_html, re.I | re.S):
            title = _clean(m.group(2))
            if len(title) < 3:
                continue
            url = "https://www.jobs.nhs.uk" + m.group(1)
            out.append(Job(title=title, company="NHS", location="United Kingdom", url=url,
                           description="", source=self.name, source_query=f"NHS: {kw}").finalize())
        return out


class CivilServiceJobsSource(Source):
    """civilservicejobs.service.gov.uk search (session + server-rendered results).
    Covers the ~100 civil-service employers whose careers pages point here."""
    name = "Civil Service Jobs"
    BASE = "https://www.civilservicejobs.service.gov.uk/csr/index.cgi"

    def __init__(self, queries=None, max_pages=2):
        self.queries = queries or ["data analyst", "data scientist"]
        self.max_pages = max_pages

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        jobs: list[Job] = []
        errors = 0
        s = requests.Session()
        s.headers.update(_UA)
        try:
            s.get(self.BASE, timeout=30)  # establish the session cookie the search needs
        except requests.RequestException:
            return SourceResult(self.name, status="error", message="could not open civil service search")
        for kw in self.queries:
            try:
                r = s.get(self.BASE, params={"what": kw, "csource": "csfsearch"}, timeout=30)
            except requests.RequestException:
                errors += 1
                continue
            if r.status_code != 200:
                errors += 1
                continue
            jobs.extend(self._parse(r.text, kw))
            if len(jobs) >= limit:
                break
        uniq = _dedupe(jobs)
        status = "ok" if (uniq or not errors) else "error"
        return SourceResult(self.name, jobs=uniq[:limit], status=status,
                            message=f"{len(uniq)} roles from {len(self.queries)} queries, {errors} errors")

    def _parse(self, page_html: str, kw: str) -> list[Job]:
        out: list[Job] = []
        # vacancy anchors link to jobs.cgi with a jcode; grab the linked title text
        for m in re.finditer(
                r'href="(https://www\.civilservicejobs\.service\.gov\.uk/csr/[^"]*jobs\.cgi[^"]*)"[^>]*>(.*?)</a>',
                page_html, re.I | re.S):
            title = _clean(m.group(2))
            if len(title) < 3:
                continue
            out.append(Job(title=title, company="UK Civil Service", location="United Kingdom",
                           url=m.group(1), description="", source=self.name,
                           source_query=f"CS: {kw}").finalize())
        return out
