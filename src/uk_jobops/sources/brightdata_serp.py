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


# --- deterministic quality gates (run in code, BEFORE the LLM, so junk wastes no tokens) ---
_STALE_HOST = re.compile(
    r"(builtin(london)?\.|builtinnyc|builtin\.com|bebee|expertini|welcometothejungle|welcome to the jungle|"
    r"app\.welcometothejungle|otta\.|datasciencejobs|stacksignal|efinancialcareers|jobrapido|neuvoo|"
    r"talent\.com|jooble|whatjobs|trabajo|learn4good|adzuna\.[a-z]+/land)", re.I)
_EXPIRED = re.compile(
    r"(no longer (accepting|available)|has been (filled|removed)|was removed|this (job|position|vacancy) "
    r"(has|was) (been )?(removed|filled|expired|closed)|position (has been |is )?filled|"
    r"not accepting applications|applications? (are |have )?closed|vacancy (has )?(closed|expired)|"
    r"\bexpired\b|closing date has passed|be an early applicant)", re.I)
_NONUK = re.compile(
    r"\b(india|mumbai|bangalore|bengaluru|hyderabad|pune|gurgaon|gurugram|chennai|noida|delhi|kolkata|"
    r"united states|u\.?s\.?a\.?|\bus hq\b|new york|san francisco|silicon valley|california|texas|boston|"
    r"chicago|dallas|miami|atlanta|alpharetta|boise|seattle|austin|denver|"
    r"canada|toronto|vancouver|montreal|ottawa|calgary|"
    r"france|paris|montrouge|saint-quentin|germany|berlin|munich|frankfurt|"
    r"spain|madrid|barcelona|portugal|lisbon|porto|netherlands|amsterdam|italy|milan|"
    r"dubai|abu dhabi|\buae\b|qatar|saudi|bahrain|"
    r"poland|krak[oó]w|warsaw|wroc[lł]aw|romania|bucharest|hungary|budapest|"
    r"singapore|hong kong|shanghai|beijing|tokyo|japan|malaysia|philippines|"
    r"australia|sydney|melbourne|brisbane|perth|new zealand|auckland|"
    r"ireland|dublin|brazil|mexico|argentina|colombia|south africa|nigeria|kenya)\b", re.I)
_UK = re.compile(
    r"\b(united kingdom|england|scotland|wales|northern ireland|\buk\b|london|manchester|"
    r"birmingham|leeds|glasgow|edinburgh|bristol|cardiff|liverpool|sheffield|newcastle|nottingham|"
    r"southampton|brighton|coventry|reading|oxford|cambridge|milton keynes|belfast|leicester|"
    r"aberdeen|dundee|stirling|swansea|remote uk|hybrid)\b", re.I)
_AGE = re.compile(r"(\d+(?:\.\d+)?)\+?\s*(day|week|month|year)s?\s+ago", re.I)
# WHITELIST: only these hosts are trusted for fresh, UK, real postings. Everything else
# (canarywharfian, bulldogjob, alooba, bebee, builtin, expertini, welcometothejungle, harnham,
# datasciencejobs, glassdoor, efinancialcareers, ...) is dropped. A company's OWN careers domain
# is trusted only for that company's own query (passed in), and must still show a UK signal.
_TRUSTED = re.compile(
    r"(reed\.co\.uk|linkedin\.com|civilservicejobs\.service\.gov\.uk|jobs\.nhs\.uk|greenhouse\.io|"
    r"lever\.co|ashbyhq\.com|smartrecruiters\.com|myworkdayjobs\.com|workable\.com|recruitee\.com|"
    r"personio\.|eightfold\.ai)", re.I)
# these are UK-only by definition -> no need to insist on a UK signal in the snippet
_UK_SAFE = re.compile(r"(reed\.co\.uk|civilservicejobs\.service\.gov\.uk|jobs\.nhs\.uk)", re.I)
_UK_CITY = re.compile(
    r"\b(London|Manchester|Birmingham|Leeds|Glasgow|Edinburgh|Bristol|Cardiff|Liverpool|Sheffield|"
    r"Newcastle|Nottingham|Southampton|Brighton|Coventry|Reading|Oxford|Cambridge|Milton Keynes|"
    r"Belfast|Leicester|Aberdeen|Dundee|Stirling|Swansea)\b")


def looks_non_uk(text: str) -> bool:
    """True when the text clearly names a non-UK location and no UK location (shared by ATS + SERP)."""
    return bool(_NONUK.search(text or "") and not _UK.search(text or ""))


def _stale_age(text: str) -> bool:
    m = _AGE.search(text or "")
    if not m:
        return False
    days = int(m.group(1)) * {"day": 1, "week": 7, "month": 30, "year": 365}[m.group(2).lower()]
    return days > 45


def _reject(title: str, desc: str, link: str) -> bool:
    """True => drop before the LLM ever sees it (non-UK / expired / stale / stale-aggregator)."""
    blob = f"{title}  {desc}"
    if _STALE_HOST.search(link):
        return True
    if _EXPIRED.search(blob):
        return True
    if _stale_age(blob):
        return True
    if _NONUK.search(blob) and not _UK.search(blob):
        return True
    return False


def _uk_location(title: str, desc: str) -> str:
    m = _UK_CITY.search(f"{title}  {desc}")
    return f"{m.group(1)}, United Kingdom" if m else "United Kingdom"


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
        self.search_domains = list(search_domains or ["uk.linkedin.com/jobs", "www.reed.co.uk/jobs"])
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

        # 1) broad / gov queries - PAST WEEK (LinkedIn/Reed re-index daily, so this kills almost all
        #    expired/'no longer accepting' postings that SERP snippets can't otherwise detect)
        for q in broad_q:
            for page in range(self.pages):
                data = self._serp(q, start=page * 10, fresh="w")
                calls += 1
                if data is None:
                    errors += 1
                    break
                found = self._extract(data, q, "", "")
                if not found:
                    break
                jobs.extend(found)

        # 2) per-company: EVERY company in the sector. Search the company's OWN careers site
        #    (site:), biased to UK; the code-level _reject() then drops any non-UK/expired result.
        queried = with_roles = 0
        with_roles_names: list[str] = []
        cats = ("(data scientist OR data analyst OR analytics OR machine learning OR data engineer) "
                "(United Kingdom OR London OR UK OR England OR Scotland OR Wales)")
        for entry in self.companies:
            name, curl = entry if isinstance(entry, (tuple, list)) else (entry, "")
            site = _site_of(curl)
            q = f"site:{site} {cats}" if site else f'"{name}" {cats}'
            data = self._serp(q, start=0, fresh="m")        # past month (career pages re-index slower)
            calls += 1
            queried += 1
            if data is None:
                errors += 1
                continue
            found = self._extract(data, q, name, site.split("/")[0] if site else "")
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

    def _serp(self, query: str, start: int = 0, fresh: str = ""):
        # fresh: "w" = past week, "m" = past month, "" = no limit. Fresher => fewer expired jobs.
        url = f"https://www.google.com/search?q={quote_plus(query)}&brd_json=1&gl={self.country}&hl=en"
        if fresh in ("w", "m"):
            url += f"&tbs=qdr:{fresh}"
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

    def _extract(self, data, query: str, company_hint: str, company_domain: str = "") -> list[Job]:
        out: list[Job] = []
        if not isinstance(data, dict):
            return out
        for it in data.get("organic", []) or []:
            if not isinstance(it, dict):
                continue
            title, link = _clean(it.get("title", "")), it.get("link", "")
            desc = _clean(it.get("description", ""))
            if not title or not link or not _is_job(link, title):
                continue
            host = urlparse(link).netloc.lower().replace("www.", "")
            on_own = bool(company_domain and host == company_domain.lower().replace("www.", ""))
            # WHITELIST: only a trusted UK/ATS host, or this company's OWN careers domain
            if not (_TRUSTED.search(host) or on_own):
                continue
            if _reject(title, desc, link):            # drop expired / stale / clearly-non-UK
                continue
            # UK-ONLY: except reed/gov (already UK), every result must show a UK signal (kills
            # non-UK LinkedIn + global company-site jobs the snippet exposes).
            if not _UK_SAFE.search(host) and not _UK.search(f"{title}  {desc}"):
                continue
            # company name: prefer the AUTHORITATIVE url slug (LinkedIn/greenhouse/lever), then the
            # site: query's company, then title parsing. Fixes 'Capgemini shown as Hugging Face' etc.
            company = self._company_from_url(link) or (company_hint if on_own else "") or self._company_from(title)
            out.append(Job(title=self._clean_title(title), company=company,
                           location=_uk_location(title, desc), url=link, description=desc,
                           source=self.name, source_query=query).finalize())
        return out

    @staticmethod
    def _company_from_url(link: str) -> str:
        """Authoritative company from the URL slug (LinkedIn '...-at-{company}-{id}', greenhouse/
        lever/ashby org path). Reliable where the SERP title/keyword is not."""
        for pat in (r"/jobs/view/.+?-at-([a-z0-9&'._-]+?)-\d{5,}",
                    r"(?:boards\.|job-boards\.)?greenhouse\.io/([^/]+)/jobs",
                    r"jobs\.lever\.co/([^/]+)/", r"jobs\.ashbyhq\.com/([^/]+)/",
                    r"([a-z0-9-]+)\.recruitee\.com"):
            m = re.search(pat, link, re.I)
            if m:
                name = m.group(1).replace("-", " ").replace("_", " ").strip()
                return " ".join(w.capitalize() for w in name.split()) if name else ""
        return ""

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
