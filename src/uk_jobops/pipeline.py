"""End-to-end orchestrator: discover -> normalise/filter/dedupe -> store -> score
-> tailor -> notify. Degrades gracefully when keys/DB are absent (useful for testing)."""
from __future__ import annotations

import json
from pathlib import Path

from . import notify
from .bucketlist import bucket_tier, load_bucket_tiers
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
        # Bright Data SERP (Google for Jobs) - the main discovery engine. Broad market queries +
        # gov/LinkedIn site: queries (only on the broad run or the relevant sector) + a rotating
        # per-company sample for the active sector. Replaces Apify.
        bd = src_cfg.get("brightdata", {})
        if bd.get("enabled") and sec.brightdata_api_key:
            from .sources.brightdata_serp import BrightDataSerpSource
            out.append(BrightDataSerpSource(
                sec.brightdata_api_key, sec.brightdata_serp_zone,
                bucket_path=self.cfg.path(bucket_path), sector=sector, run_broad=run_broad,
                extra_queries=bd.get("extra_queries", []),
                site_queries=self._gov_site_queries(sector, run_broad, bd),
                top_companies_per_run=bd.get("top_companies_per_run", 5),
                max_queries=bd.get("max_queries", 20), pages=bd.get("pages", 1),
                country=bd.get("country", "gb")))
        # Optional HTML gov portals (disabled by default now that SERP covers gov via site: queries).
        gov = src_cfg.get("gov", {})
        if gov.get("enabled"):
            from .sources.gov import CivilServiceJobsSource, NHSJobsSource
            gq = gov.get("queries", ["data analyst", "data scientist"])
            cs_sectors = [s.lower() for s in gov.get("civil_service_sectors", ["civil services"])]
            nhs_sectors = [s.lower() for s in gov.get("nhs_sectors", ["insurance & health"])]
            if run_broad or (sector and sector.lower() in cs_sectors):
                out.append(CivilServiceJobsSource(queries=gq, max_pages=gov.get("max_pages", 2)))
            if run_broad or (sector and sector.lower() in nhs_sectors):
                out.append(NHSJobsSource(queries=gq, max_pages=gov.get("max_pages", 2)))
        # Legacy Apify (disabled by default; kept as a fallback if you top up credits).
        ap = src_cfg.get("apify", {})
        if ap.get("enabled") and sec.apify_tokens:
            from .sources.apify_google import ApifyGoogleSource
            out.append(ApifyGoogleSource(
                sec.apify_tokens, self.cfg.path(bucket_path),
                actor=ap.get("actor", "johnvc~google-jobs-scraper"),
                top_companies_per_run=ap.get("top_companies_per_run", 5),
                num_results=ap.get("num_results", 50), max_queries=ap.get("max_queries", 6),
                extra_queries=ap.get("extra_queries", []), sector=sector, run_broad=run_broad))
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
            statuses.append({"source": res.source, "status": res.status, "count": len(res.jobs), "message": res.message})
        return jobs, statuses

    def run(self, mode: str = "recurring", sector: str | None = None) -> dict:
        search = self.s.get("search", {})
        recency = search.get("recency_days_first", 14) if mode == "first" else search.get("recency_days_recurring", 1)
        scoring = self.s.get("scoring", {})

        rot = self.s.get("rotation", {})
        sectors = rot.get("sectors", [])
        broad_index = rot.get("apify_broad_on_index", -1)
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
        tiers = load_bucket_tiers(
            self.cfg.path(self.s.get("bucket_list", {}).get("path", "data/companies_bucketlist.csv")))
        for j in targets:
            j.bucket_tier = bucket_tier(j.company, tiers)
            j.in_bucket = bool(j.bucket_tier)

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
                   "category_data_analysis": sum(1 for j in targets if j.category == "data-analysis"),
                   "bucket_only_dropped": bucket_only_dropped,
                   "sources": statuses, "scored": 0, "tailored": 0}
        if sector:
            from .bucketlist import companies_in_sector
            summary["companies_searched"] = len(companies_in_sector(
                self.cfg.path(self.s.get("bucket_list", {}).get("path", "data/companies_master.csv")), sector))
        else:
            _ap = self.s.get("sources", {}).get("apify", {})
            summary["companies_searched"] = (_ap.get("top_companies_per_run", 0)
                                             if _ap.get("enabled") and self.cfg.secrets.apify_tokens else 0)

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

        # fit scoring + tailoring need LLM keys. Capped per run + resilient to
        # free-tier rate limits (a 429 stops the LLM phase cleanly and resumes next run).
        if self.cfg.secrets.gemini_api_key or self.cfg.secrets.groq_api_key:
            import time

            from .cv.render_docx import render
            from .llm.client import LLM, LLMError
            from .llm.fit_score import score_fit, score_fit_batch
            from .llm.tailor import tailor

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

            # if scoring already exhausted the quota, don't waste a call on tailoring
            tailor_jobs = ([] if llm_exhausted else
                           store.jobs_to_tailor(scoring.get("tailor_threshold", 70),
                                                limit=scoring.get("max_tailor_per_run", 6)))
            rulebook = (self.cfg.path(lc.get("rulebook", "config/rulebook.md")).read_text(encoding="utf-8")
                        if tailor_jobs else "")
            for job in tailor_jobs:
                try:
                    t = tailor(llm, rulebook, self.cfg.base_cv, job, max_repair=lc.get("max_repair_loops", 1),
                           profile=self.cfg.profile)
                except LLMError as exc:
                    errors.append(str(exc)[:140])
                    if _rate_limited(exc):
                        summary["llm_note"] = "Rate limit during tailoring; remaining jobs continue next run."
                        break
                    continue
                cv_path, cover_path = render(self.cfg.base_cv, t, job)
                # store the .docx bytes in the DB so both dashboards can offer
                # downloads anywhere (the runner's files are otherwise ephemeral)
                cvb = Path(cv_path).read_bytes() if cv_path and Path(cv_path).exists() else None
                covb = Path(cover_path).read_bytes() if cover_path and Path(cover_path).exists() else None
                store.update(job["dedupe_key"], cv_path=cv_path, cover_path=cover_path,
                             cv_blob=cvb, cover_blob=covb, status="tailored", gaps=t.gaps)
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
            alerts = store.jobs_to_notify(ncfg.get("min_fit", 75), limit=ncfg.get("max_per_run", 10))
            first_name = (self.s.get("candidate", {}).get("name", "there") or "there").split()[0]
            hb_ok, hb_detail = True, "off"
            if ncfg.get("heartbeat", True):
                src_line = " · ".join(f"{s.get('source', '?').split()[0]} {s.get('count', 0)}"
                                      for s in summary.get("sources", []))
                _title = (f"✅ *{sector} sector* complete" if sector
                          else "🔔 *Job Search Assistant* — run complete")
                hb = (f"{_title}\n"
                      f"Discovered {summary.get('discovered', 0)} · new {summary.get('stored_new', 0)} · "
                      f"scored {summary.get('scored', 0)} · tailored {summary.get('tailored', 0)}\n"
                      + (f"📥 {src_line}\n" if src_line else "")
                      + f"🧭 DS {summary.get('category_data_science', 0)} · "
                      + f"DA {summary.get('category_data_analysis', 0)}\n"
                      + f"🔎 {summary.get('companies_searched', 0)} target companies searched · "
                      + f"{len(alerts)} new alerts below")
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
