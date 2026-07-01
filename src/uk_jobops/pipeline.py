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

    def _sources(self):
        sec = self.cfg.secrets
        src_cfg = self.s.get("sources", {})
        out = []
        if src_cfg.get("reed", {}).get("enabled"):
            out.append(ReedSource(sec.reed_api_key))
        if src_cfg.get("adzuna", {}).get("enabled"):
            out.append(AdzunaSource(sec.adzuna_app_id, sec.adzuna_app_key,
                                    src_cfg.get("adzuna", {}).get("country", "gb")))
        if src_cfg.get("ats", {}).get("enabled"):
            out.append(ATSSource(self.s.get("bucket_list", {}).get("path", "data/companies_bucketlist.csv"),
                                 self.s.get("seniority", {}).get("include", [])))
        ap = src_cfg.get("apify", {})
        if ap.get("enabled") and sec.apify_tokens:
            from .sources.apify_google import ApifyGoogleSource
            out.append(ApifyGoogleSource(
                sec.apify_tokens,
                self.cfg.path(self.s.get("bucket_list", {}).get("path", "data/companies_bucketlist.csv")),
                actor=ap.get("actor", "johnvc~google-jobs-scraper"),
                top_companies_per_run=ap.get("top_companies_per_run", 5),
                num_results=ap.get("num_results", 50), max_queries=ap.get("max_queries", 6)))
        return out

    def discover(self, recency_days: int):
        search = self.s.get("search", {})
        jobs, statuses = [], []
        for src in self._sources():
            res = src.fetch(queries=search.get("queries", []), locations=search.get("locations", []),
                            recency_days=recency_days, limit=search.get("max_per_source", 100))
            jobs.extend(res.jobs)
            statuses.append({"source": res.source, "status": res.status, "count": len(res.jobs), "message": res.message})
        return jobs, statuses

    def run(self, mode: str = "recurring") -> dict:
        search = self.s.get("search", {})
        recency = search.get("recency_days_first", 14) if mode == "first" else search.get("recency_days_recurring", 1)
        scoring = self.s.get("scoring", {})

        raw, statuses = self.discover(recency)
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

        summary = {"mode": mode, "discovered": len(raw), "targets": len(targets),
                   "rejected": len(rejected), "bucket_matches": sum(1 for j in targets if j.in_bucket),
                   "top100_matches": sum(1 for j in targets if j.bucket_tier == "top100"),
                   "sources": statuses, "scored": 0, "tailored": 0}

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
                    results = score_fit_batch(llm, self.cfg.base_cv, chunk)
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
                            results[idx] = score_fit(llm, self.cfg.base_cv, job)
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
                    t = tailor(llm, rulebook, self.cfg.base_cv, job, max_repair=lc.get("max_repair_loops", 1))
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
                hb = (f"🔔 *Job Search Assistant* — run complete\n"
                      f"Discovered {summary.get('discovered', 0)} · new {summary.get('stored_new', 0)} · "
                      f"scored {summary.get('scored', 0)} · tailored {summary.get('tailored', 0)}\n"
                      f"New alerts below: {len(alerts)}")
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


def run(mode: str = "recurring") -> dict:
    return Pipeline(load_config()).run(mode)
