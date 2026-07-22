"""End-to-end orchestrator: discover -> normalise/filter/dedupe -> store -> score
-> tailor -> notify. Degrades gracefully when keys/DB are absent (useful for testing)."""
from __future__ import annotations

import json
from pathlib import Path

from . import notify
from .bucketlist import bucket_tier, company_sector, load_bucket_tiers, load_company_sectors
from .config import Config, load_config
from .dedupe import dedupe
from .filtering import apply_filters
from .normalize import normalize
from .sources.adzuna import AdzunaSource
from .sources.ats import ATSSource
from .sources.reed import ReedSource


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.s = cfg.settings

    def _sources(self, sector: str | None = None, run_broad: bool = True):
        sec = self.cfg.secrets
        src_cfg = self.s.get("sources", {})
        bucket_path = self.s.get("bucket_list", {}).get("path", "data/companies_master.csv")
        out = []
        # Broad market APIs (Reed/Adzuna) refresh the whole UK market - run only on the
        # designated broad run so a 7x/day sector rotation doesn't burn their daily quotas.
        if run_broad and src_cfg.get("reed", {}).get("enabled"):
            out.append(ReedSource(sec.reed_api_key))
        if run_broad and src_cfg.get("adzuna", {}).get("enabled"):
            out.append(AdzunaSource(sec.adzuna_app_id, sec.adzuna_app_key,
                                    src_cfg.get("adzuna", {}).get("country", "gb")))
        # ATS scans this sector's companies (free, accurate) - every sector run.
        if src_cfg.get("ats", {}).get("enabled"):
            out.append(ATSSource(bucket_path, self.s.get("seniority", {}).get("include", []), sector=sector))
        # Cheap STRUCTURED LinkedIn via Apify, filtered by companyName -> searches EACH of this
        # sector's list companies on LinkedIn (real per-company coverage). Sector runs only.
        al = src_cfg.get("apify_linkedin", {})
        if sector and al.get("enabled") and sec.apify_tokens:
            from .bucketlist import companies_in_sector
            from .sources.apify_linkedin import ApifyLinkedInSource
            out.append(ApifyLinkedInSource(
                sec.apify_tokens, companies_in_sector(self.cfg.path(bucket_path), sector),
                actor=al.get("actor", "valig~linkedin-jobs-scraper"), title_queries=al.get("title_queries"),
                location=al.get("location", "United Kingdom"), date_posted=al.get("date_posted", "r604800"),
                batch_size=al.get("batch_size", 40), max_jobs_per_company=al.get("max_jobs_per_company", 6),
                max_batches=al.get("max_batches", 8)))
        # Bright Data SERP (Google) - the main discovery engine. Broad job-board queries +
        # gov/LinkedIn site: queries (on the broad run or the relevant sector) + a rotating
        # per-company sample for the active sector.
        # STRUCTURED LinkedIn (Bright Data dataset) - accurate company/location/active-status.
        # Runs on the broad/full run (keyword discovery is market-wide). When active, SERP stops
        # searching LinkedIn (structured is better) to avoid duplicate, lower-quality LinkedIn rows.
        li = src_cfg.get("linkedin", {})
        linkedin_on = bool(run_broad and li.get("enabled") and sec.brightdata_api_key
                           and sec.brightdata_linkedin_dataset)
        if linkedin_on:
            from .sources.brightdata_linkedin import BrightDataLinkedInSource
            out.append(BrightDataLinkedInSource(
                sec.brightdata_api_key, sec.brightdata_linkedin_dataset,
                keywords=li.get("keywords"), location=li.get("location", "United Kingdom"),
                country=li.get("country", "GB"), time_range=li.get("time_range", "Past month"),
                max_wait=li.get("max_wait", 480), max_age_days=li.get("max_age_days", 30)))

        bd = src_cfg.get("brightdata", {})
        if bd.get("enabled"):     # ALWAYS add when enabled; the source reports 'skipped (no key)' if the
                                  # BRIGHTDATA_API_KEY is missing -> you can SEE it's not connected in the logs
            from .bucketlist import companies_in_sector
            from .sources.ats import detect_ats
            from .sources.brightdata_serp import BrightDataSerpSource
            _domains = bd.get("search_domains")
            if linkedin_on:                       # structured LinkedIn handles LinkedIn -> drop it from SERP
                _domains = [d for d in (_domains or []) if "linkedin" not in d.lower()] or ["www.reed.co.uk/jobs"]
            # sector run => every company. Companies whose careers page is a known ATS are handled by
            # ATSSource (structured, real UK location); SERP only searches the REST (no double-search,
            # saves credits). Broad run => market queries only.
            _all = companies_in_sector(self.cfg.path(bucket_path), sector) if sector else []
            companies = [(n, u) for (n, u) in _all if not detect_ats(u)]
            out.append(BrightDataSerpSource(
                sec.brightdata_api_key, sec.brightdata_serp_zone,
                sector=sector, run_broad=run_broad,
                extra_queries=bd.get("extra_queries", []),
                site_queries=self._gov_site_queries(sector, run_broad, bd),
                search_domains=_domains, companies=companies,
                max_queries=bd.get("max_queries", 20), pages=bd.get("pages", 1),
                country=bd.get("country", "gb")))
        return out

    def _gov_site_queries(self, sector, run_broad, bd) -> list[str]:
        """site:-restricted SERP queries for the government portals (and optionally LinkedIn),
        added on the broad run or on the run for the relevant sector."""
        cats = bd.get("gov_queries", ["data analyst", "data scientist", "data engineer"])
        cs = [s.lower() for s in bd.get("civil_service_sectors", ["civil services"])]
        nhs = [s.lower() for s in bd.get("nhs_sectors", ["insurance & health"])]
        q: list[str] = []
        if run_broad or (sector and sector.lower() in cs):
            q += [f"site:civilservicejobs.service.gov.uk {c}" for c in cats]
        if run_broad or (sector and sector.lower() in nhs):
            q += [f"site:jobs.nhs.uk {c}" for c in cats]
        if run_broad and bd.get("linkedin_site"):
            q += [f"site:uk.linkedin.com/jobs {c} United Kingdom" for c in cats]
        return q

    def discover(self, recency_days: int, sector: str | None = None, run_broad: bool = True):
        search = self.s.get("search", {})
        jobs, statuses = [], []
        for src in self._sources(sector, run_broad):
            res = src.fetch(queries=search.get("queries", []), locations=search.get("locations", []),
                            recency_days=recency_days, limit=search.get("max_per_source", 100))
            jobs.extend(res.jobs)
            statuses.append({"source": res.source, "status": res.status, "count": len(res.jobs),
                             "message": res.message, "meta": res.meta})
        return jobs, statuses

    def run(self, mode: str = "recurring", sector: str | None = None) -> dict:
        search = self.s.get("search", {})
        recency = search.get("recency_days_first", 14) if mode == "first" else search.get("recency_days_recurring", 1)
        scoring = self.s.get("scoring", {})

        rot = self.s.get("rotation", {})
        sectors = rot.get("sectors", [])
        broad_index = rot.get("broad_on_index", -1)
        # A dedicated daily 'full' run (sector=None) does the broad market sweep (Reed/Adzuna +
        # broad SERP + gov + LinkedIn). The 7 sector runs focus purely on their sector's companies
        # (ATS + per-company SERP), unless a sector is explicitly the designated broad index.
        run_broad = True if not (sector and sector in sectors) else (sectors.index(sector) == broad_index)
        raw, statuses = self.discover(recency, sector=sector, run_broad=run_broad)
        normalize(raw)
        sen = self.s.get("seniority", {})
        targets, rejected = apply_filters(raw, sen.get("include", []), sen.get("exclude_title", []),
                                          sen.get("exclude_company", []),
                                          exclude_recruiters=sen.get("exclude_recruiters", True))
        targets = dedupe(targets)

        # Boost: tag jobs by bucket tier so top-100 target companies jump the
        # scoring/tailoring queue (db queries order top100 first, then any bucket).
        _bpath = self.cfg.path(self.s.get("bucket_list", {}).get("path", "data/companies_master.csv"))
        tiers = load_bucket_tiers(_bpath)
        sec_map = load_company_sectors(_bpath)
        for j in targets:
            j.bucket_tier = bucket_tier(j.company, tiers)
            j.in_bucket = bool(j.bucket_tier)
            j.sector = company_sector(j.company, sec_map)

        # strict mode: keep ONLY jobs from bucket-list target companies (max focus)
        bucket_only_dropped = 0
        if self.s.get("search", {}).get("require_bucket", False):
            _before = len(targets)
            targets = [j for j in targets if j.in_bucket]
            bucket_only_dropped = _before - len(targets)

        summary = {"mode": mode, "sector": sector or "ALL", "run_broad": run_broad,
                   "discovered": len(raw), "targets": len(targets),
                   "rejected": len(rejected), "bucket_matches": sum(1 for j in targets if j.in_bucket),
                   "top100_matches": sum(1 for j in targets if j.bucket_tier == "top100"),
                   "category_data_science": sum(1 for j in targets if j.category == "data-science"),
                   "category_ai_engineer": sum(1 for j in targets if j.category == "ai-engineer"),
                   "category_data_analysis": sum(1 for j in targets if j.category == "data-analysis"),
                   "bucket_only_dropped": bucket_only_dropped,
                   "sources": statuses, "scored": 0, "tailored": 0}
        if sector:
            from .bucketlist import companies_in_sector
            _total = len(companies_in_sector(
                self.cfg.path(self.s.get("bucket_list", {}).get("path", "data/companies_master.csv")), sector))
            # companies with a KEPT role this run (via ATS API or SERP), by their tagged sector
            _with = sorted({j.company for j in targets if getattr(j, "sector", "") == sector and j.company})
            summary["companies_in_sector"] = _total
            summary["companies_searched"] = _total          # every company searched (ATS API or SERP)
            summary["companies_with_roles"] = len(_with)
            summary["companies_with_roles_names"] = _with[:80]
            summary["companies_missed"] = 0
        else:
            _bd_meta = next((s.get("meta", {}) for s in statuses if "Bright Data" in s.get("source", "")), {})
            summary["companies_searched"] = _bd_meta.get("companies_queried", 0)
            summary["companies_with_roles"] = _bd_meta.get("companies_with_roles", 0)
            summary["companies_with_roles_names"] = _bd_meta.get("with_roles_names", [])

        # snapshot for offline inspection
        Path("output").mkdir(exist_ok=True)
        Path("output/discovered.json").write_text(
            json.dumps([t.to_db() for t in targets[:200]], indent=2), encoding="utf-8")

        db_url = self.cfg.secrets.supabase_db_url
        if not db_url:
            summary["note"] = "No SUPABASE_DB_URL: discovered jobs written to output/discovered.json only."
            Path("output/last_run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return summary

        from .db import Store

        store = Store(db_url)
        store.init_schema()
        new, dup = store.upsert_jobs(targets)
        summary["stored_new"], summary["stored_dup"] = new, dup
        # collapse any legacy/cross-location duplicates already in the table,
        # keeping the most-progressed row - runs BEFORE scoring so we never spend
        # LLM calls on the same role twice.
        purged = store.purge_excluded(sen.get("exclude_title", []), sen.get("exclude_company", []),
                                      exclude_recruiters=sen.get("exclude_recruiters", True))
        if purged:
            summary["purged_excluded"] = purged
        legacy = store.purge_sources(self.s.get("cleanup", {}).get("purge_sources", ["Apify"]))
        if legacy:
            summary["purged_legacy"] = legacy
        despam = store.purge_spam()
        if despam:
            summary["purged_spam"] = despam
        collapsed = store.collapse_duplicates()
        if collapsed:
            summary["collapsed_duplicates"] = collapsed

        # fit scoring + tailoring need LLM keys. Capped per run + resilient to
        # free-tier rate limits (a 429 stops the LLM phase cleanly and resumes next run).
        if self.cfg.secrets.gemini_api_key or self.cfg.secrets.groq_api_key:
            import time

            from .llm.client import LLM, LLMError
            from .llm.fit_score import score_fit, score_fit_batch
            from .llm.recommend import recommend

            llm = LLM(self.cfg)
            lc = self.s.get("llm", {})
            delay = float(lc.get("request_delay_seconds", 1.0))
            errors: list[str] = []
            llm_exhausted = False

            def _rate_limited(exc) -> bool:
                m = str(exc).lower()
                return "429" in m or "rate limit" in m or "quota" in m or "resource_exhausted" in m

            # Batch-score many jobs per LLM call (far fewer calls + tokens => stays inside
            # free-tier rate limits and clears the backlog faster).
            shortlist_th = scoring.get("shortlist_threshold", 60)
            to_score = store.jobs_needing_score(limit=scoring.get("max_score_per_run", 40))
            bsize = max(1, int(scoring.get("score_batch_size", 8)))
            for start in range(0, len(to_score), bsize):
                chunk = to_score[start:start + bsize]
                try:
                    results = score_fit_batch(llm, self.cfg.base_cv, chunk, self.cfg.profile)
                except LLMError as exc:
                    errors.append(str(exc)[:140])
                    if _rate_limited(exc):
                        summary["llm_note"] = "Rate limit during scoring; remaining jobs continue next run."
                        llm_exhausted = True
                        break
                    # malformed batch JSON: fall back to scoring this chunk one by one
                    results = {}
                    for idx, job in enumerate(chunk):
                        try:
                            results[idx] = score_fit(llm, self.cfg.base_cv, job, self.cfg.profile)
                        except LLMError as exc2:
                            if _rate_limited(exc2):
                                llm_exhausted = True
                                break
                # SECOND OPINION: verify DeepSeek's high-fit picks with Gemini (two-model consensus).
                # Only the high-fit subset -> token-cheap. Keep the LOWER score (conservative) and
                # OR the ghost flags, so a job only stays high if BOTH models agree.
                vth = scoring.get("verify_threshold", 0)
                if vth and not llm_exhausted:
                    high = [(i, job) for i, job in enumerate(chunk)
                            if results.get(i) and results[i].score >= vth]
                    if high:
                        try:
                            vres = score_fit_batch(llm, self.cfg.base_cv, [j for _, j in high], self.cfg.profile,
                                                   provider=lc.get("tailor_provider"), model=lc.get("tailor_model"))
                            for k, (i, _job) in enumerate(high):
                                v = vres.get(k)
                                if v:
                                    if v.score < results[i].score:
                                        results[i].score, results[i].reasoning = v.score, v.reasoning or results[i].reasoning
                                    results[i].ghost_flag = results[i].ghost_flag or v.ghost_flag
                            summary["verified"] = summary.get("verified", 0) + len(high)
                        except LLMError as exc:
                            errors.append("verify: " + str(exc)[:120])
                for idx, job in enumerate(chunk):
                    fit = results.get(idx)
                    if fit is None:
                        continue
                    status = "shortlisted" if fit.score >= shortlist_th else "scored"
                    store.update(job["dedupe_key"], fit_score=fit.score, fit_reasoning=fit.reasoning,
                                 ghost_flag=fit.ghost_flag, status=status, gaps=fit.gaps)
                    summary["scored"] += 1
                if llm_exhausted:
                    break
                time.sleep(delay)

            # if scoring already exhausted the quota, don't waste a call on recommendations
            rec_jobs = ([] if llm_exhausted else
                        store.jobs_to_recommend(scoring.get("tailor_threshold", 70),
                                                limit=scoring.get("max_tailor_per_run", 8)))
            for job in rec_jobs:
                try:
                    rec = recommend(llm, self.cfg.base_cv, job, self.cfg.profile)
                except LLMError as exc:
                    errors.append(str(exc)[:140])
                    if _rate_limited(exc):
                        summary["llm_note"] = "Rate limit during recommendations; remaining jobs continue next run."
                        break
                    continue
                store.update(job["dedupe_key"], recommendations=rec.to_markdown(),
                             cover_text=rec.cover_letter, status="tailored", gaps=rec.gaps)
                summary["tailored"] += 1
                time.sleep(delay)
            if errors:
                summary["llm_errors"] = errors[:5]
        else:
            summary["note"] = "No LLM key: stored jobs but skipped scoring/tailoring."

        digest = store.digest(min_fit=scoring.get("tailor_threshold", 70))
        notify.write_digest(digest)
        # Telegram: a per-run heartbeat (so you always get a message + can see failures)
        # plus one rich alert per new high-fit role. Errors are surfaced in the summary.
        tg = self.cfg.secrets
        if tg.telegram_bot_token and tg.telegram_chat_id:
            ncfg = self.s.get("notify", {})
            max_alerts = ncfg.get("max_per_run", 10)
            # fetch a larger fresh pool, then keep ONLY top companies + government (no startups)
            pool = store.jobs_to_notify(ncfg.get("min_fit", 75), limit=max(max_alerts * 8, 40))
            if ncfg.get("top_gov_only", True):
                allow = notify.load_notify_allowlist(
                    self.cfg.path(ncfg.get("companies_file", "data/notify_companies.txt")))
                govs = ncfg.get("gov_sectors", ["Civil Services"])
                pool = [a for a in pool
                        if notify.is_top_or_gov(a.get("company", ""), a.get("sector", ""), allow, govs)]
            alerts = pool[:max_alerts]
            first_name = (self.s.get("candidate", {}).get("name", "there") or "there").split()[0]
            hb_ok, hb_detail = True, "off"
            if ncfg.get("heartbeat", True):
                src_line = " · ".join(f"{s.get('source', '?').split()[0]} {s.get('count', 0)}"
                                      for s in summary.get("sources", []))
                import html as _html
                _title = (f"✅ <b>{_html.escape(sector)} sector</b> complete" if sector
                          else "🔔 <b>Job Search Assistant</b> — run complete")
                hb = (f"{_title}\n"
                      f"Discovered {summary.get('discovered', 0)} · new {summary.get('stored_new', 0)} · "
                      f"scored {summary.get('scored', 0)} · tailored {summary.get('tailored', 0)}\n"
                      + (f"📥 {src_line}\n" if src_line else "")
                      + f"🧭 DS {summary.get('category_data_science', 0)} · "
                      + f"AI {summary.get('category_ai_engineer', 0)} · "
                      + f"DA {summary.get('category_data_analysis', 0)}\n"
                      + (f"🔎 {summary.get('companies_searched', 0)}/{summary.get('companies_in_sector', '?')} "
                         f"companies searched · {summary.get('companies_with_roles', 0)} with roles\n"
                         if sector else
                         f"🔎 {summary.get('companies_searched', 0)} companies searched\n")
                      + f"{len(alerts)} new alerts below")
                # connectivity warnings so you can SEE at a glance if a key isn't wired
                src_cfg = self.s.get("sources", {})
                if src_cfg.get("brightdata", {}).get("enabled") and not tg.brightdata_api_key:
                    hb += "\n⚠️ BRIGHTDATA_API_KEY not set — LinkedIn (Bright Data) is OFF"
                if summary.get("llm_note"):
                    hb += f"\n⚠️ {summary['llm_note']}"
                hb_ok, hb_detail = notify.send_message(tg.telegram_bot_token, tg.telegram_chat_id, hb)
            sent, aerr = notify.send_job_alerts(alerts, tg.telegram_bot_token, tg.telegram_chat_id, first_name)
            if sent:
                store.mark_notified([a["dedupe_key"] for a in alerts])
            summary["telegram"] = (f"heartbeat={'ok' if hb_ok else 'FAIL: ' + hb_detail}; "
                                   f"alerts_sent={sent}" + (f"; alert_error={aerr}" if aerr else ""))

        # persist run history (dashboard reads these) + a local snapshot
        store.log_run(summary)
        Path("output/last_run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        store.close()
        return summary


def run(mode: str = "recurring", sector: str | None = None) -> dict:
    return Pipeline(load_config()).run(mode, sector)
