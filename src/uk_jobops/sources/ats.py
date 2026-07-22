"""Company ATS APIs (free, accurate) for your target companies.
Detects Greenhouse / Lever / Ashby / SmartRecruiters / Workday from each careers URL
and pulls live postings via their public JSON endpoints. Optionally scoped to one sector.
Master List CSV columns: company_name, sector, careers_url"""
from __future__ import annotations

import csv
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from ..models import Job
from .base import Source, SourceResult
from .brightdata_serp import ats_uk_ok

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# ATS board links that companies embed inside their own careers landing page.
# When careers_url is a marketing page (e.g. careers.company.com) the real board
# lives here - we follow the page once (free, no anti-bot on most career pages)
# and pull the ATS API. Ordered so the most specific hosts win.
_ATS_LINK = re.compile(
    r"https?://[^\s\"'<>)]*?(?:"
    r"(?:boards|job-boards)\.greenhouse\.io/[A-Za-z0-9_-]+"
    r"|jobs\.lever\.co/[A-Za-z0-9_-]+"
    r"|jobs\.ashbyhq\.com/[A-Za-z0-9_-]+"
    r"|(?:careers|jobs)\.smartrecruiters\.com/[A-Za-z0-9_&.-]+"
    r"|apply\.workable\.com/[A-Za-z0-9_-]+"
    r"|[A-Za-z0-9_-]+\.workable\.com"
    r"|[A-Za-z0-9_-]+\.recruitee\.com"
    r"|[A-Za-z0-9_-]+\.eightfold\.ai"
    r"|[A-Za-z0-9_-]+\.jobs\.personio\.(?:com|de)"
    r"|[A-Za-z0-9_-]+\.breezy\.hr"
    r"|[A-Za-z0-9_-]+\.teamtailor\.com"
    r"|[A-Za-z0-9-]+\.wd\d+\.myworkdayjobs\.com/[^\s\"'<>)]+"
    r")", re.I)
# Greenhouse/Ashby often load the board via a JS embed that only names the org token.
_EMBED = [
    (re.compile(r"greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]+)", re.I), "greenhouse"),
    (re.compile(r'(?:data-|")gh[-_]?src["\s=]+[^"\']*?boards\.greenhouse\.io/([A-Za-z0-9_-]+)', re.I), "greenhouse"),
    (re.compile(r"ashbyhq\.com/([A-Za-z0-9_-]+)/embed", re.I), "ashby"),
]


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
        (r"://([A-Za-z0-9_-]+)\.jobs\.personio\.(?:com|de)", "personio"),
        (r"://([A-Za-z0-9_-]+)\.breezy\.hr", "breezy"),
        (r"://([A-Za-z0-9_-]+)\.teamtailor\.com", "teamtailor"),
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


def resolve_ats(url: str, *, follow: bool = True, timeout: int = 12) -> tuple[str, str] | None:
    """Best-effort: return (ats, token) for a careers URL.
    1) if the URL itself is a known ATS -> use it directly (free, no fetch).
    2) else fetch the careers landing page once and detect the ATS board embedded/
       linked inside it (careers.company.com -> its Greenhouse/Lever/Workday board).
    Marketing career pages are rarely anti-bot, so this is a free coverage win.
    Returns None when the page is JS-only/custom (those fall through to Bright Data)."""
    direct = detect_ats(url)
    if direct or not follow or not url:
        return direct
    try:
        r = requests.get(url, timeout=timeout, headers=_UA, allow_redirects=True)
    except requests.RequestException:
        return None
    # a redirect may land us straight on the ATS host
    landed = detect_ats(r.url)
    if landed:
        return landed
    if r.status_code != 200 or not r.text:
        return None
    html = r.text
    m = _ATS_LINK.search(html)
    if m:
        found = detect_ats(m.group(0))
        if found:
            return found
    for pat, ats in _EMBED:            # JS-embed boards that only name the org token
        e = pat.search(html)
        if e:
            return ats, e.group(1)
    return None


class ATSSource(Source):
    name = "Company ATS"

    def __init__(self, bucket_list_path: str, include_terms: list[str], sector: str | None = None,
                 follow_careers: bool = True, max_workers: int = 8):
        self.path = Path(bucket_list_path)
        self.include = [t.lower() for t in include_terms]
        self.sector = sector
        self.follow_careers = follow_careers      # classify custom careers pages to find embedded ATS boards
        self.max_workers = max_workers

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
        errors = scanned = with_roles = classified = 0
        with_roles_names: list[str] = []

        def work(item: tuple[str, str]):
            """Resolve a company's ATS (direct or by following its careers page) and pull UK roles."""
            company, url = item
            direct = bool(detect_ats(url))
            detected = resolve_ats(url, follow=self.follow_careers)
            if not detected:
                return None
            ats, token = detected
            try:
                # ATS data has an authoritative location -> STRICT UK gate (drop foreign roles)
                pulled = [j for j in self._pull(ats, token, company)
                          if self._matches(j.title) and ats_uk_ok(j.location)]
            except requests.RequestException:
                return (company, "error", direct)
            return (company, pulled, direct)

        # threaded: most time is network wait (careers-page fetches + ATS APIs)
        with ThreadPoolExecutor(max_workers=max(1, self.max_workers)) as ex:
            futures = [ex.submit(work, c) for c in companies]
            for fut in as_completed(futures):
                res = fut.result()
                if res is None:
                    continue
                company, pulled, direct = res
                scanned += 1
                if not direct:
                    classified += 1          # covered only because we followed the careers page
                if pulled == "error":
                    errors += 1
                    continue
                if pulled:
                    with_roles += 1
                    with_roles_names.append(company)
                    jobs.extend(pulled)

        meta = {"companies_queried": scanned, "companies_with_roles": with_roles,
                "classified_from_careers": classified, "with_roles_names": with_roles_names[:60]}
        return SourceResult(self.name, jobs=jobs[:limit], meta=meta,
                            message=f"{scanned} companies on a known ATS ({classified} found via careers page) · "
                                    f"{len(jobs)} UK matched roles · {errors} errors")

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
                # Ashby often labels the job "United Kingdom" while the real office (address/secondary
                # location) is abroad -> build a COMBINED location so ats_uk_ok sees the foreign country.
                parts = [it.get("locationName") or it.get("location") or ""]
                for sl in it.get("secondaryLocations") or []:
                    if isinstance(sl, dict):
                        loc = sl.get("location")
                        parts.append(loc.get("name", "") if isinstance(loc, dict)
                                     else (sl.get("locationName") or (loc if isinstance(loc, str) else "")))
                addr = (it.get("address") or {}).get("postalAddress") or {}
                parts += [addr.get("addressLocality", ""), addr.get("addressRegion", ""),
                          addr.get("addressCountry", "")]
                locstr = ", ".join(dict.fromkeys(p for p in parts if p))   # de-dupe, keep order
                out.append(Job(title=it.get("title", ""), company=company,
                               location=locstr, url=it.get("jobUrl", ""),
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
        elif ats == "breezy":
            # Breezy exposes a free public JSON feed with a real location per role.
            r = requests.get(f"https://{token}.breezy.hr/json", timeout=30, headers=_UA)
            r.raise_for_status()
            payload = r.json()
            for it in payload if isinstance(payload, list) else []:
                loc = it.get("location") or {}
                country = loc.get("country")
                country = country.get("name") if isinstance(country, dict) else country
                locstr = loc.get("name") or ", ".join(x for x in [loc.get("city"), country] if x)
                out.append(Job(title=it.get("name", ""), company=company,
                               location=locstr or "", url=it.get("url") or "",
                               description=it.get("description", "") or "",
                               posted_date=str(it.get("published_date", "")), source=self.name).finalize())
        elif ats == "teamtailor":
            # Teamtailor's API needs a key, but each careers page embeds JSON-LD JobPosting blocks
            # (title + jobLocation) - parse those (free, structured, real location).
            import json as _json
            r = requests.get(f"https://{token}.teamtailor.com/jobs", timeout=20, headers=_UA)
            r.raise_for_status()
            for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', r.text, re.S):
                try:
                    data = _json.loads(block.strip())
                except ValueError:
                    continue
                items = data if isinstance(data, list) else [data]
                for it in items:
                    if not isinstance(it, dict) or it.get("@type") != "JobPosting":
                        continue
                    addr = ((it.get("jobLocation") or {}).get("address") or {}) if isinstance(it.get("jobLocation"), dict) else {}
                    locstr = ", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion"),
                                                   addr.get("addressCountry")] if x)
                    out.append(Job(title=it.get("title", ""), company=company, location=locstr,
                                   url=it.get("url") or f"https://{token}.teamtailor.com/jobs",
                                   description="", posted_date=str(it.get("datePosted", "")),
                                   source=self.name).finalize())
        elif ats == "personio":
            import xml.etree.ElementTree as ET
            r = requests.get(f"https://{token}.jobs.personio.com/xml", timeout=30, headers=_UA)
            r.raise_for_status()
            for pos in ET.fromstring(r.content).iter("position"):
                pid = pos.findtext("id") or ""
                out.append(Job(title=pos.findtext("name") or "", company=company,
                               location=pos.findtext("office") or "United Kingdom",
                               url=f"https://{token}.jobs.personio.com/job/{pid}" if pid else "",
                               description="", source=self.name).finalize())
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
