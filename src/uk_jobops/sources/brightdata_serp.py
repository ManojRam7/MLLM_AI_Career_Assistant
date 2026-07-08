"""Google job discovery via the Bright Data SERP API (brd_json organic results).

Two modes:
  - BROAD run: market-wide board-scoped queries + gov site: queries.
  - SECTOR run: one combined query for EVERY company in the sector, so we can report exactly
    which companies were searched, which had roles, and which were missed.

Only genuine INDIVIDUAL job postings are kept (LinkedIn view / Reed slug-ID / NHS jobadvert /
Civil-Service jcode / Greenhouse-Lever-Ashby / job-listing). Listing/search pages
(Glassdoor 'SRCH_', Reed '...-jobs') are dropped. Fails gracefully."""
from __future__ import annotations

import html
import re
from urllib.parse import quote_plus, urlparse

import requests

from ..models import Job
from .base import Source, SourceResult

ENDPOINT = "https://api.brightdata.com/request"
_TAG = re.compile(r"<[^>]+>")

# ACCEPT: URL looks like ONE specific posting. Broad enough to catch a company's OWN careers
# site (Goldman higher.gs.com/roles/ID, Barclays search.jobs/job/ID, Workday .../job/...) - not
# just the big boards - because manual verification showed many target-company jobs live there.
_INDIVIDUAL = re.compile(
    r"(jobs\.nhs\.uk/candidate/jobadvert/|"
    r"linkedin\.com/jobs/view/|"
    r"/jobs?/view/|"
    r"/(job|jobs|role|roles|vacancy|vacancies|opening|openings|position|posting|advert|listing)/[^?#]*\d|"
    r"jcode=|gh_jid=|/jobadvert/|[?&]jobid=|[?&]jobId=|"
    r"greenhouse\.io/[^/]+/jobs/\d|"
    r"lever\.co/[^/]+/[0-9a-f-]{8}|"
    r"ashbyhq\.com/[^/]+/[0-9a-f-]{8}|"
    r"myworkdayjobs\.com/.+/[A-Za-z0-9_-]*_?R?-?\d{3,}|"
    r"smartrecruiters\.com/[^/]+/\d|"
    r"workable\.com/[a-z]+/[A-F0-9]{6}|"
    r"alooba\.com/[a-z]{2}/job/|"
    r"higher\.gs\.com/roles/\d|"
    r"reed\.co\.uk/jobs/[^/]+/\d|totaljobs\.com/job/\d|cv-library\.co\.uk/job/\d|"
    r"glassdoor\.[a-z.]+/job-listing/|indeed\.[a-z.]+/(viewjob|rc/clk))", re.I)
# REJECT: URL is a search/listing/category page
_LISTING = re.compile(
    r"(SRCH_|[a-z0-9-]+-jobs(\b|/|\?|$)|/jobs(\?|$)|/jobs-in-|/jobs/search|glassdoor\.[a-z.]+/Job/|"
    r"/browse[/?]|/search[/?]|/results[/?]|/category/|/all-jobs)", re.I)
# REJECT: title is a listing ("1043 data scientist jobs", "Data Analyst Jobs", "... jobs in London")
_LISTING_TITLE = re.compile(r"(^\s*[\d,]+\s+.*\bjobs\b|\bjobs\b\s*$|\bjobs?\s+in\b)", re.I)


def _clean(s: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", s or "")).split())


_ATS_HOSTS = ("boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co",
              "jobs.ashbyhq.com", "apply.workable.com", "careers.smartrecruiters.com")


def _site_of(careers_url: str) -> str:
    """Turn a company's careers_url into a Google `site:` value so we search THAT company's own
    careers site (e.g. higher.gs.com, boards.greenhouse.io/monzo, tesco.wd3.myworkdayjobs.com)."""
    try:
        p = urlparse(careers_url if "://" in careers_url else "https://" + careers_url)
        host = p.netloc.replace("www.", "").strip()
        if not host or "google." in host or "civilservicejobs" in host or "jobs.nhs.uk" in host:
            return ""                      # gov handled separately; skip generic aggregators
        seg = [s for s in p.path.split("/") if s]
        if host in _ATS_HOSTS and seg:     # ATS boards need the org path (…greenhouse.io/monzo)
            return f"{host}/{seg[0]}"
        return host
    except Exception:
        return ""


def _is_job(url: str, title: str) -> bool:
    return bool(_INDIVIDUAL.search(url) and not _LISTING.search(url) and not _LISTING_TITLE.search(title))


class BrightDataSerpSource(Source):
    name = "Google (Bright Data)"

    def __init__(self, api_key, zone="serp", *, sector=None, run_broad=True,
                 extra_queries=None, site_queries=None, search_domains=None,
                 companies=None, max_queries=22, pages=1, country="gb"):
        self.api_key = api_key
        self.zone = zone or "serp"
        self.sector = sector
        self.run_broad = run_broad
        self.extra_queries = list(extra_queries or [])
        self.site_queries = list(site_queries or [])
        self.search_domains = list(search_domains or [
            "uk.linkedin.com/jobs", "www.reed.co.uk/jobs", "www.totaljobs.com",
            "www.cv-library.co.uk", "uk.indeed.com", "www.glassdoor.co.uk"])
        self.companies = list(companies or [])       # per-company search targets (sector run = ALL)
        self.max_queries = max_queries               # caps only the broad market queries
        self.pages = max(1, pages)
        self.country = country
        self._first_error = ""

    def _board_filter(self) -> str:
        return "(" + " OR ".join(f"site:{d}" for d in self.search_domains) + ")"

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        if not self.api_key:
            return SourceResult(self.name, status="skipped", message="no BRIGHTDATA_API_KEY set")
        board = self._board_filter()
        broad_q: list[str] = []
        if self.run_broad:
            broad_q = [f"{q} {board}" for q in (self.extra_queries + list(queries[:2]))] + self.site_queries
            broad_q = list(dict.fromkeys(broad_q))[:self.max_queries]
        else:
            broad_q = list(self.site_queries)        # gov site: queries still run on a gov sector run

        jobs: list[Job] = []
        errors = calls = 0

        # 1) broad / gov queries
        for q in broad_q:
            for page in range(self.pages):
                data = self._serp(q, start=page * 10)
                calls += 1
                if data is None:
                    errors += 1
                    break
                found = self._extract(data, q, "")
                if not found:
                    break
                jobs.extend(found)

        # 2) per-company: EVERY company in the sector. Prefer the company's OWN careers site
        #    (site:) so we find their real openings; fall back to a name query.
        queried = with_roles = 0
        with_roles_names: list[str] = []
        cats = "(data scientist OR data analyst OR analytics OR machine learning OR data engineer)"
        for entry in self.companies:
            name, curl = entry if isinstance(entry, (tuple, list)) else (entry, "")
            site = _site_of(curl)
            q = (f"site:{site} {cats}" if site
                 else f'"{name}" {cats} United Kingdom')
            data = self._serp(q, start=0)
            calls += 1
            queried += 1
            if data is None:
                errors += 1
                continue
            found = self._extract(data, q, name)
            if found:
                with_roles += 1
                with_roles_names.append(name)
                jobs.extend(found)

        seen, uniq = set(), []
        for j in jobs:
            if not j.url or j.url in seen:
                continue
            seen.add(j.url)
            uniq.append(j)
        status = "ok" if (uniq or not errors) else "error"
        meta = {"companies_queried": queried, "companies_with_roles": with_roles,
                "with_roles_names": with_roles_names[:60]}
        msg = f"{len(uniq)} jobs · {len(broad_q)} broad + {queried} company queries · {errors} errors"
        if self._first_error:
            msg += f" · first_error: {self._first_error[:80]}"
        return SourceResult(self.name, jobs=uniq[:limit], status=status, message=msg, meta=meta)

    def _serp(self, query: str, start: int = 0):
        url = f"https://www.google.com/search?q={quote_plus(query)}&brd_json=1&gl={self.country}&hl=en"
        if start:
            url += f"&start={start}"
        try:
            r = requests.post(ENDPOINT,
                              headers={"Authorization": f"Bearer {self.api_key}",
                                       "Content-Type": "application/json"},
                              json={"zone": self.zone, "url": url, "format": "raw"}, timeout=60)
            if r.status_code in (200, 201):
                try:
                    return r.json()
                except ValueError:
                    return {}
            if not self._first_error:
                self._first_error = f"HTTP {r.status_code}: {r.text[:100]}"
            return None
        except requests.RequestException as exc:
            if not self._first_error:
                self._first_error = str(exc)[:100]
            return None

    def _extract(self, data, query: str, company_hint: str) -> list[Job]:
        out: list[Job] = []
        if not isinstance(data, dict):
            return out
        for it in data.get("organic", []) or []:
            if not isinstance(it, dict):
                continue
            title, link = _clean(it.get("title", "")), it.get("link", "")
            if not title or not link or not _is_job(link, title):
                continue
            out.append(Job(title=self._clean_title(title), company=company_hint or self._company_from(title),
                           location="United Kingdom", url=link,
                           description=_clean(it.get("description", "")),
                           source=self.name, source_query=query).finalize())
        return out

    @staticmethod
    def _company_from(title: str) -> str:
        m = re.match(r"(.+?)\s+hiring\s+", title)
        if m:
            return m.group(1).strip()
        m = re.search(r"\bat\s+([A-Z][\w&.,'\- ]{1,40})", title)
        if m:
            return m.group(1).strip(" -|·")
        return ""

    @staticmethod
    def _clean_title(title: str) -> str:
        t = re.sub(r"\s*[|\-–]\s*(LinkedIn|Reed\.co\.uk|Totaljobs|CV-Library|Indeed|Glassdoor|jobs\.nhs\.uk|"
                   r"Civil Service Jobs).*$", "", title, flags=re.I)
        t = re.sub(r"^.+?\s+hiring\s+", "", t, flags=re.I)
        t = re.sub(r"\s+in\s+[A-Z][\w ,]+$", "", t)
        return t.strip() or title
