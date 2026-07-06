"""Company ATS APIs (free, accurate) for your target companies.
Detects Greenhouse / Lever / Ashby / SmartRecruiters / Workday from each careers URL
and pulls live postings via their public JSON endpoints. Optionally scoped to one sector.
Master List CSV columns: company_name, sector, careers_url"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import requests

from ..models import Job
from .base import Source, SourceResult

_UA = {"User-Agent": "Mozilla/5.0 (compatible; jobops/1.0)"}


def detect_ats(url: str) -> tuple[str, str] | None:
    """Return (ats, token) from a careers URL, or None. For Workday the token packs
    'tenant|datacenter|site' (site keeps its original case - the API path is case-sensitive)."""
    u = url or ""
    for pat, ats in [
        (r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)", "greenhouse"),
        (r"jobs\.lever\.co/([A-Za-z0-9_-]+)", "lever"),
        (r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)", "ashby"),
        (r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_&.-]+)", "smartrecruiters"),
        (r"apply\.workable\.com/([A-Za-z0-9_-]+)", "workable"),
        (r"://([A-Za-z0-9_-]+)\.recruitee\.com", "recruitee"),
        (r"://([A-Za-z0-9_-]+)\.eightfold\.ai", "eightfold"),
    ]:
        m = re.search(pat, u, re.I)
        if m:
            return ats, m.group(1).split("/")[0]
    m = re.search(r"://([A-Za-z0-9_-]+)\.workable\.com", u, re.I)     # subdomain form
    if m and m.group(1).lower() not in ("www", "apply"):
        return "workable", m.group(1)
    m = re.search(r"://([A-Za-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[A-Za-z]{2}-[A-Za-z]{2}/)?([A-Za-z0-9_-]+)",
                  u, re.I)
    if m:
        return "workday", f"{m.group(1).lower()}|{m.group(2).lower()}|{m.group(3)}"
    return None


class ATSSource(Source):
    name = "Company ATS"

    def __init__(self, bucket_list_path: str, include_terms: list[str], sector: str | None = None):
        self.path = Path(bucket_list_path)
        self.include = [t.lower() for t in include_terms]
        self.sector = sector

    def _companies(self) -> list[tuple[str, str]]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("company_name") or row.get("Company Name") or "").strip()
                url = (row.get("careers_url") or row.get("Careers Page") or "").strip()
                sec = (row.get("sector") or "").strip()
                if self.sector and sec.lower() != self.sector.strip().lower():
                    continue
                if name and url:
                    out.append((name, url))
        return out

    def _matches(self, title: str) -> bool:
        t = title.lower()
        return any(term in t for term in self.include)

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        companies = self._companies()
        if not companies:
            return SourceResult(self.name, status="skipped",
                                message=f"No companies at {self.path}" + (f" for {self.sector}" if self.sector else ""))
        jobs: list[Job] = []
        errors = scanned = 0
        for company, url in companies:
            if len(jobs) >= limit:
                break
            detected = detect_ats(url)
            if not detected:
                continue
            scanned += 1
            ats, token = detected
            try:
                jobs.extend(self._pull(ats, token, company))
            except requests.RequestException:
                errors += 1
        jobs = [j for j in jobs if self._matches(j.title)]
        return SourceResult(self.name, jobs=jobs[:limit],
                            message=f"{len(companies)} companies, {scanned} on known ATS, "
                                    f"{len(jobs)} matched roles, {errors} errors")

    def _pull(self, ats: str, token: str, company: str) -> list[Job]:
        out: list[Job] = []
        if ats == "greenhouse":
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
                             timeout=30, headers=_UA)
            r.raise_for_status()
            for it in r.json().get("jobs", []):
                out.append(Job(title=it.get("title", ""), company=company,
                               location=(it.get("location") or {}).get("name", ""),
                               url=it.get("absolute_url", ""), description=it.get("content", ""),
                               posted_date=it.get("updated_at", ""), source=self.name).finalize())
        elif ats == "lever":
            r = requests.get(f"https://api.lever.co/v0/postings/{token}?mode=json", timeout=30, headers=_UA)
            r.raise_for_status()
            for it in r.json():
                cats = it.get("categories", {})
                out.append(Job(title=it.get("text", ""), company=company,
                               location=cats.get("location", ""), url=it.get("hostedUrl", ""),
                               description=it.get("descriptionPlain", ""),
                               posted_date=str(it.get("createdAt", "")), source=self.name).finalize())
        elif ats == "ashby":
            r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", timeout=30, headers=_UA)
            r.raise_for_status()
            for it in r.json().get("jobs", []):
                out.append(Job(title=it.get("title", ""), company=company,
                               location=it.get("locationName", ""), url=it.get("jobUrl", ""),
                               description=it.get("descriptionPlain", ""), source=self.name).finalize())
        elif ats == "smartrecruiters":
            r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100",
                             timeout=30, headers=_UA)
            r.raise_for_status()
            for it in r.json().get("content", []):
                loc = it.get("location") or {}
                locstr = ", ".join(x for x in [loc.get("city"), loc.get("country")] if x)
                url = it.get("ref") or f"https://jobs.smartrecruiters.com/{token}/{it.get('id')}"
                out.append(Job(title=it.get("name", ""), company=company, location=locstr, url=url,
                               description="", posted_date=str(it.get("releasedDate", "")),
                               source=self.name).finalize())
        elif ats == "workable":
            r = requests.get(f"https://apply.workable.com/api/v3/accounts/{token}/jobs",
                             timeout=30, headers=_UA)
            r.raise_for_status()
            for it in r.json().get("results", []):
                loc = it.get("location") or {}
                locstr = ", ".join(x for x in [loc.get("city"), loc.get("country")] if x)
                out.append(Job(title=it.get("title", ""), company=company, location=locstr,
                               url=it.get("url") or it.get("application_url") or "",
                               description=it.get("description", ""), source=self.name).finalize())
        elif ats == "recruitee":
            r = requests.get(f"https://{token}.recruitee.com/api/offers/", timeout=30, headers=_UA)
            r.raise_for_status()
            for it in r.json().get("offers", []):
                out.append(Job(title=it.get("title", ""), company=company,
                               location=it.get("location", "") or it.get("city", ""),
                               url=it.get("careers_url") or it.get("careers_apply_url") or "",
                               description=it.get("description", ""), source=self.name).finalize())
        elif ats == "eightfold":
            r = requests.get(f"https://{token}.eightfold.ai/api/apply/v2/jobs",
                             params={"num": 50, "start": 0, "domain": f"{token}.com"},
                             timeout=30, headers=_UA)
            r.raise_for_status()
            for it in r.json().get("positions", []):
                out.append(Job(title=it.get("name", ""), company=company,
                               location=it.get("location", "") or "United Kingdom",
                               url=it.get("canonicalPositionUrl") or it.get("positionUrl") or "",
                               description=it.get("job_description", "") or "", source=self.name).finalize())
        elif ats == "workday":
            tenant, dc, site = token.split("|")
            api = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
            r = requests.post(api, json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "data"},
                              timeout=30, headers={**_UA, "Content-Type": "application/json"})
            r.raise_for_status()
            for it in r.json().get("jobPostings", []):
                ext = it.get("externalPath", "")
                url = f"https://{tenant}.{dc}.myworkdayjobs.com/{site}{ext}" if ext else ""
                out.append(Job(title=it.get("title", ""), company=company,
                               location=it.get("locationsText", ""), url=url, description="",
                               posted_date=it.get("postedOn", ""), source=self.name).finalize())
        return out
