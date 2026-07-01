"""Google Jobs discovery via the Apify actor johnvc/google-jobs-scraper.

Google Jobs aggregates postings from LinkedIn, Indeed, Glassdoor, Totaljobs,
CV-Library and company boards, so this one source widens coverage a lot. It runs a
few generic data-science queries PLUS a rotating sample of the top-100 target
companies (bucket-list-driven search), and rotates through multiple Apify tokens so
a spent free-tier account fails over to the next. Resilient: any failure returns an
error status and the pipeline continues with the other sources."""
from __future__ import annotations

from ..models import Job
from .base import Source, SourceResult

ENDPOINT = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"


class ApifyGoogleSource(Source):
    name = "Google (Apify)"

    def __init__(self, tokens, bucket_path=None, *, actor="johnvc~google-jobs-scraper",
                 top_companies_per_run=5, num_results=50, max_queries=6, extra_queries=None):
        self.tokens = [t for t in (tokens or []) if t]
        self.bucket_path = bucket_path
        self.actor = actor
        self.top_companies_per_run = top_companies_per_run
        self.num_results = num_results
        self.max_queries = max_queries
        self.extra_queries = list(extra_queries or [])

    def fetch(self, *, queries, locations, recency_days, limit) -> SourceResult:
        if not self.tokens:
            return SourceResult(self.name, status="skipped", message="no APIFY tokens set")
        # high-value always-run queries (NHS/civil service/gov) + a couple of generic ones
        q_list = list(dict.fromkeys(self.extra_queries + list(queries[:2])))
        if self.bucket_path:
            from ..bucketlist import sample_top_companies
            for c in sample_top_companies(self.bucket_path, self.top_companies_per_run):
                q_list.append(f"{c} data scientist")
        q_list = q_list[:self.max_queries]

        jobs: list[Job] = []
        errors = 0
        for q in q_list:
            items = self._run(q)
            if items is None:
                errors += 1
                continue
            jobs.extend(self._to_job(q, it) for it in items if isinstance(it, dict))
            if len(jobs) >= limit:
                break
        status = "ok" if (jobs or not errors) else "error"
        return SourceResult(self.name, jobs=jobs[:limit], status=status,
                            message=f"{len(jobs)} from {len(q_list)} queries, {errors} errors")

    def _run(self, query: str):
        """Run the actor for one query, rotating tokens on auth/quota failure.
        Returns a list of dataset items, or None if every token failed."""
        import requests

        body = {"query": query, "location": "United Kingdom", "country": "uk",
                "google_domain": "google.co.uk", "num_results": self.num_results,
                "max_pagination": max(3, self.num_results // 10)}
        url = ENDPOINT.format(actor=self.actor)
        for token in self.tokens:
            try:
                r = requests.post(url, params={"token": token, "timeout": 150}, json=body, timeout=180)
                if r.status_code in (200, 201):
                    data = r.json()
                    return data if isinstance(data, list) else []
                # 401/402/403/429 => token spent or unauthorised; try the next one
            except Exception:
                continue
        return None

    @staticmethod
    def _to_job(query: str, it: dict) -> Job:
        url = (it.get("share_link") or it.get("link") or it.get("job_link")
               or it.get("apply_link") or "")
        if not url:
            ap = it.get("apply_options") or it.get("apply_links") or []
            if isinstance(ap, list) and ap:
                first = ap[0]
                url = (first.get("link") if isinstance(first, dict) else str(first)) or ""
        ext = it.get("detected_extensions") if isinstance(it.get("detected_extensions"), dict) else {}
        posted = ext.get("posted_at") or it.get("posted_at") or it.get("date") or ""
        return Job(
            title=it.get("title", "") or "",
            company=it.get("company_name") or it.get("company") or it.get("companyName") or "",
            location=it.get("location", "") or "United Kingdom",
            url=url,
            description=it.get("description", "") or it.get("job_description", "") or "",
            posted_date=str(posted),
            source="Google (Apify)",
            source_query=query,
        ).finalize()
