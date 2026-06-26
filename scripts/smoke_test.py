"""End-to-end smoke test / doctor. Run locally after putting keys in .env:

    pip install -r requirements.txt
    python scripts/smoke_test.py

It NEVER prints your secret values - only PASS/FAIL and counts. Paste the output
to debug. It does a tiny run (1 query, a few results) so it costs ~nothing.
"""
from __future__ import annotations

import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from uk_jobops.config import load_config  # noqa: E402

TICK, CROSS = "PASS", "----"


def line(ok: bool, label: str, extra: str = "") -> None:
    print(f"  [{TICK if ok else CROSS}] {label}{(' - ' + extra) if extra else ''}")


def main() -> None:
    cfg = load_config()
    s = cfg.secrets
    print("\n== 1. Keys present (values never shown) ==")
    line(bool(s.reed_api_key), "Reed")
    line(bool(s.adzuna_app_id and s.adzuna_app_key), "Adzuna")
    line(bool(s.gemini_api_key), "Gemini")
    line(bool(s.groq_api_key), "Groq")
    line(bool(s.supabase_db_url), "Supabase")

    sample = None
    print("\n== 2. Discovery (1 query, limit 5 per source) ==")
    from uk_jobops.normalize import normalize
    from uk_jobops.sources.adzuna import AdzunaSource
    from uk_jobops.sources.ats import ATSSource
    from uk_jobops.sources.reed import ReedSource

    srcs = [
        ReedSource(s.reed_api_key),
        AdzunaSource(s.adzuna_app_id, s.adzuna_app_key, cfg.settings.get("sources", {}).get("adzuna", {}).get("country", "gb")),
        ATSSource(cfg.settings.get("bucket_list", {}).get("path", "data/companies_bucketlist.csv"),
                  cfg.settings.get("seniority", {}).get("include", [])),
    ]
    for src in srcs:
        try:
            res = src.fetch(queries=["data scientist"], locations=["United Kingdom"], recency_days=14, limit=5)
            normalize(res.jobs)
            line(res.status in {"ok", "skipped"}, src.name, f"{res.status}: {len(res.jobs)} jobs - {res.message[:80]}")
            if res.jobs and sample is None:
                sample = res.jobs[0]
                print(f"        e.g. {sample.title} @ {sample.company} ({sample.location})")
        except Exception as exc:
            line(False, src.name, f"ERROR {exc}")

    print("\n== 3. LLM fit-score + tailor (1 job) ==")
    if not (s.gemini_api_key or s.groq_api_key):
        line(False, "LLM", "no Gemini/Groq key - skipped")
    else:
        try:
            from uk_jobops.cv.render_docx import render
            from uk_jobops.llm.client import LLM
            from uk_jobops.llm.fit_score import score_fit
            from uk_jobops.llm.tailor import tailor
            from uk_jobops.llm.validator import validate

            job = (sample.to_db() if sample else
                   {"title": "Data Scientist", "company": "Example UK", "location": "London",
                    "description": "We need Python, SQL, machine learning, marketing analytics, "
                                   "experimentation and stakeholder communication.", "dedupe_key": "smoke"})
            llm = LLM(cfg)
            fit = score_fit(llm, cfg.base_cv, job)
            line(fit.score > 0, "Fit scoring", f"score={fit.score} band={fit.band}")
            rulebook = cfg.path(cfg.settings.get("llm", {}).get("rulebook", "config/rulebook.md")).read_text(encoding="utf-8")
            t = tailor(llm, rulebook, cfg.base_cv, job, max_repair=1)
            ok, issues = validate(t)
            line(ok, "Tailor + validate", f"coverage={t.keyword_coverage} issues={len(issues)}")
            if issues:
                for i in issues[:4]:
                    print(f"        - {i}")
            cv_path, cover_path = render(cfg.base_cv, t, job, out_root="output/smoke")
            line(bool(cv_path), "Render docx", cv_path)
        except Exception as exc:
            line(False, "LLM", f"ERROR {exc}")
            traceback.print_exc()

    print("\n== 4. Supabase ==")
    if not s.supabase_db_url:
        line(False, "Supabase", "no SUPABASE_DB_URL - skipped")
    else:
        try:
            from uk_jobops.db import Store

            store = Store(s.supabase_db_url)
            store.init_schema()
            if sample:
                store.upsert_jobs([sample])
            store.close()
            line(True, "Supabase", "connected + schema ready + upsert OK")
        except Exception as exc:
            line(False, "Supabase", f"ERROR {exc}")

    print("\nDone. Paste this output to debug any FAIL.")


if __name__ == "__main__":
    main()
