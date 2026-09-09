"""Build the detailed per-run report (2.0) — the single source of truth that is shown on BOTH the
website (stored in summary_json) and Telegram.

It answers, every run:
  - how many companies from the bucket list were searched, how many returned roles, and which ones
  - how many important (target-company) jobs were picked, and from which companies
  - what every OTHER search operation did (Reed / Adzuna / Company ATS / Google-SERP sub-tracks)
  - the best jobs of the run, ranked by importance, each with the CV keywords to add

Pure formatting over plain dicts/lists — no I/O, no LLM, no DB — so it is trivially testable."""
from __future__ import annotations

import html
from typing import Any


def _e(s: Any) -> str:
    return html.escape(str(s or ""))


# friendly labels for the raw source names
_SRC_LABEL = {
    "Reed": "Reed API", "Adzuna": "Adzuna API", "Company ATS": "Company ATS boards",
    "Google/SERP (Bright Data)": "Google / SERP (all boards)",
    "LinkedIn Jobs (Bright Data)": "LinkedIn (structured)", "Indeed (Bright Data)": "Indeed (structured)",
}


def build_report(summary: dict, targets: list, best: list[dict]) -> dict:
    """targets = this run's KEPT Job objects (attrs: company, title, in_bucket, bucket_tier, sector,
    source). best = ranked best-jobs dicts from Store.best_jobs()."""
    # --- bucket-list coverage (this run) ---
    bucket_targets = [j for j in targets if getattr(j, "in_bucket", False) and getattr(j, "company", "")]
    by_company: dict[str, list[str]] = {}
    for j in bucket_targets:
        by_company.setdefault(j.company, []).append(getattr(j, "title", "") or "role")
    top100_now = sum(1 for j in bucket_targets if getattr(j, "bucket_tier", "") == "top100")

    total_companies = summary.get("companies_in_sector") or summary.get("companies_searched") or 0
    bucket = {
        "total_companies": total_companies,
        "companies_searched": summary.get("companies_searched", 0),
        "companies_with_roles": len(by_company),
        "with_roles_names": sorted(by_company.keys()),
        "important_jobs_this_run": len(bucket_targets),
        "top100_jobs_this_run": top100_now,
        "by_company": {c: by_company[c] for c in sorted(by_company)},
    }

    # --- per-source breakdown ---
    sources = []
    google_tracks = {}
    for s in summary.get("sources", []):
        name = s.get("source", "?")
        row = {"name": _SRC_LABEL.get(name, name), "raw": name,
               "status": s.get("status", "?"), "count": s.get("count", 0),
               "detail": s.get("message", "")}
        sources.append(row)
        if "Bright Data" in name and s.get("meta"):
            m = s["meta"]
            google_tracks = {"linkedin": m.get("linkedin_jobs", 0), "market": m.get("market_jobs", 0),
                             "gov": m.get("gov_jobs", 0), "company_sites": m.get("company_site_jobs", 0),
                             "company_queries": m.get("company_queries", 0)}

    # --- best jobs (ranked by importance) ---
    best_rows = []
    for b in best:
        best_rows.append({
            "title": b.get("title", ""), "company": b.get("company", ""),
            "fit": int(b.get("fit_score") or 0),
            "tier": b.get("bucket_tier", ""), "in_bucket": bool(b.get("in_bucket")),
            "location": b.get("locations") or b.get("location") or "",
            "url": b.get("url", ""), "keywords": b.get("cv_keywords") or "",
            "category": b.get("category", ""), "sector": b.get("sector", ""),
            "country": b.get("country") or "", "visa": b.get("visa_sponsorship") or "",
        })

    return {
        "scope": summary.get("sector", "ALL"),
        "mode": summary.get("mode", ""),
        "totals": {
            "discovered": summary.get("discovered", 0), "new": summary.get("stored_new", 0),
            "targets": summary.get("targets", 0), "scored": summary.get("scored", 0),
            "tailored": summary.get("tailored", 0),
            "ds": summary.get("category_data_science", 0), "ai": summary.get("category_ai_engineer", 0),
            "da": summary.get("category_data_analysis", 0),
            "bucket_matches": summary.get("bucket_matches", 0),
            "top100_matches": summary.get("top100_matches", 0),
            "apply_ready": summary.get("apply_ready", 0),
        },
        "bucket": bucket, "sources": sources, "google_tracks": google_tracks,
        "best": best_rows,
        "llm_note": summary.get("llm_note", ""),
    }


# --------------------------------------------------------------------------- markdown (website/file)
def report_markdown(r: dict) -> str:
    t = r["totals"]; b = r["bucket"]; g = r.get("google_tracks", {})
    p: list[str] = [f"# Run report — {r['scope']}", ""]
    p.append(f"**Discovered** {t['discovered']} · **new** {t['new']} · **kept** {t['targets']} · "
             f"**scored** {t['scored']} · **tailored** {t['tailored']}")
    p.append(f"**Categories** — Data Science {t['ds']} · AI/ML {t['ai']} · Data Analysis {t['da']}")
    p.append("")
    p.append("## Bucket list (your target companies)")
    p.append(f"- Companies on the list: **{b['total_companies']}**")
    p.append(f"- Companies searched this run: **{b['companies_searched']}**")
    p.append(f"- Companies that returned roles: **{b['companies_with_roles']}**")
    p.append(f"- Important (target-company) jobs picked: **{b['important_jobs_this_run']}** "
             f"(top-tier: {b['top100_jobs_this_run']})")
    if b["by_company"]:
        p.append("")
        p.append("**Roles found, by target company:**")
        for c, titles in b["by_company"].items():
            p.append(f"- **{c}** — {'; '.join(titles[:6])}")
    p.append("")
    p.append("## Search operations")
    for s in r["sources"]:
        p.append(f"- **{s['name']}**: {s['status']} — {s['count']} jobs — {s['detail']}")
    if g:
        p.append(f"- **Google sub-tracks** — LinkedIn {g.get('linkedin',0)} · other boards {g.get('market',0)} "
                 f"· government {g.get('gov',0)} · company career-sites {g.get('company_sites',0)} "
                 f"(from {g.get('company_queries',0)} company queries)")
    p.append("")
    p.append("## Best jobs this run")
    if not r["best"]:
        p.append("_No scored jobs to surface yet._")
    for j in r["best"]:
        star = " ⭐" if j["tier"] == "top100" else (" ◆" if j["in_bucket"] else "")
        p.append(f"### {j['title']} — {j['company']}{star}  ·  fit {j['fit']}/100")
        meta = " · ".join(x for x in [j["location"], j["category"], j["sector"]] if x)
        if meta:
            p.append(meta)
        if j.get("country") and j["country"] not in ("United Kingdom", "Unknown"):
            p.append(f"🌍 {j['country']} · visa: {j.get('visa') or 'unknown'}")
        if j["keywords"]:
            p.append(f"**{j['keywords']}**")
        if j["url"]:
            p.append(f"[open job]({j['url']})")
        p.append("")
    return "\n".join(p)


# --------------------------------------------------------------------------- Telegram (summary card)
def report_telegram_summary(r: dict, name: str = "there") -> str:
    t = r["totals"]; b = r["bucket"]; g = r.get("google_tracks", {})
    lines = [f"🧭 <b>Job Search — run report ({_e(r['scope'])})</b>",
             f"Discovered <b>{t['discovered']}</b> · new <b>{t['new']}</b> · kept <b>{t['targets']}</b> · "
             f"scored <b>{t['scored']}</b>",
             f"DS {t['ds']} · AI/ML {t['ai']} · DA {t['da']}",
             "",
             "⭐ <b>Bucket list</b>",
             f"• {b['companies_searched']}/{b['total_companies']} companies searched · "
             f"{b['companies_with_roles']} returned roles",
             f"• {b['important_jobs_this_run']} target-company jobs picked "
             f"({b['top100_jobs_this_run']} top-tier)"]
    if b["with_roles_names"]:
        shown = ", ".join(b["with_roles_names"][:14])
        more = len(b["with_roles_names"]) - 14
        lines.append(f"• From: {_e(shown)}" + (f" +{more} more" if more > 0 else ""))
    lines.append("")
    lines.append("📥 <b>Sources</b>")
    for s in r["sources"]:
        emoji = "✅" if s["status"] == "ok" else ("⚠️" if s["status"] == "skipped" else "❌")
        lines.append(f"{emoji} {_e(s['name'])}: {s['count']}")
    if g:
        lines.append(f"🔎 Google — LinkedIn {g.get('linkedin',0)} · boards {g.get('market',0)} · "
                     f"gov {g.get('gov',0)} · career-sites {g.get('company_sites',0)}")
    if r.get("llm_note"):
        lines.append(f"⚠️ {_e(r['llm_note'])}")
    if t.get("apply_ready"):
        lines.append(f"🚀 <b>{t['apply_ready']} role(s) ready to apply</b> (direct ATS ≥ threshold) — "
                     f"run auto_apply.py")
    lines.append("")
    lines.append(f"⬇️ Top {len(r['best'])} picks below, {_e(name)}")
    return "\n".join(lines)


def best_job_telegram(j: dict, name: str = "there") -> str:
    """One rich alert per best job — now includes the CV keywords to add for THIS role."""
    fit = int(j.get("fit") or j.get("fit_score") or 0)
    if fit >= 85:
        emoji, level = "🎯", "Highly recommended"
    elif fit >= 70:
        emoji, level = "👍", "Strong match"
    else:
        emoji, level = "🔎", "Worth a look"
    tier = j.get("tier") or j.get("bucket_tier") or ""
    tag = "  ⭐ <b>top-tier target</b>" if tier == "top100" else ("  ◆ <b>target company</b>" if j.get("in_bucket") else "")
    loc = _e(j.get("location") or j.get("locations") or "")
    parts = [f"{emoji} <b>{_e(level)}</b>{tag}",
             f"<b>{_e(j.get('title'))}</b> — {_e(j.get('company'))}",
             f"📊 Fit <b>{fit}/100</b>" + (f" · 📍 {loc}" if loc else "")]
    # global: show country + visa signal so non-UK roles are clearly flagged
    country = j.get("country") or ""
    visa = j.get("visa") or j.get("visa_sponsorship") or ""
    if country and country not in ("United Kingdom", "Unknown"):
        vtag = {"sponsors": "🛂 sponsors visa", "likely": "🛂 likely sponsor",
                "refused": "⛔ no sponsorship", "unknown": "🛂 sponsorship unclear"}.get(visa, "")
        parts.append(f"🌍 <b>{_e(country)}</b>" + (f" · {vtag}" if vtag else ""))
    kw = j.get("keywords") or j.get("cv_keywords") or ""
    if kw:
        parts.append(f"📝 {_e(kw)}")
    if j.get("url"):
        parts.append(f'🔗 <a href="{_e(j.get("url"))}">open job</a>')
    return "\n".join(parts)
