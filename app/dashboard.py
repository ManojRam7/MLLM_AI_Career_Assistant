"""uk-jobops dashboard - jobs, pipeline runs, LLM config and an editable tracker,
all backed by the same Supabase database the pipeline writes to.

Run locally:   streamlit run app/dashboard.py
Deploy free:   Streamlit Community Cloud, with SUPABASE_DB_URL in app secrets.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from uk_jobops.config import load_config            # noqa: E402
from uk_jobops.db import Store                       # noqa: E402

STATUSES = ["new", "scored", "shortlisted", "tailored", "applied", "assessment",
            "assessment_cleared", "interview", "offer", "rejected"]

st.set_page_config(page_title="Job Search Assistant", page_icon="🧭", layout="wide")


def get_db_url() -> str:
    # env / .env first (local) so we never probe st.secrets when it isn't needed
    url = os.environ.get("SUPABASE_DB_URL", "") or (load_config().secrets.supabase_db_url or "")
    if url:
        return url
    try:  # Streamlit Community Cloud
        return str(st.secrets.get("SUPABASE_DB_URL", ""))
    except Exception:
        return ""


@st.cache_resource(show_spinner=False)
def get_store(url: str) -> Store:
    s = Store(url)
    s.init_schema()
    return s


@st.cache_data(ttl=60, show_spinner=False)
def load_jobs(url: str) -> pd.DataFrame:
    import datetime as _dt

    df = pd.DataFrame(get_store(url).all_jobs())
    if df.empty:
        return df
    n = len(df)
    blank = pd.Series([""] * n, index=df.index)
    # resilient to schema drift: guarantee every column the UI reads exists
    for _col, _def in (("tracked", False), ("is_custom", False), ("in_bucket", False), ("bucket_tier", ""),
                       ("category", ""), ("notes", ""), ("source", ""), ("status", "new"), ("url", ""),
                       ("cv_path", ""), ("fit_reasoning", "")):
        if _col not in df.columns:
            df[_col] = _def
    df["tracked"] = df["tracked"].fillna(False).astype(bool)
    df["is_custom"] = df["is_custom"].fillna(False).astype(bool)
    df["notes"] = df["notes"].fillna("")
    df["fit_score"] = df["fit_score"].fillna(0).astype(int)
    df["fit"] = df["fit_score"].where(df["status"] != "new")  # blank for not-yet-scored jobs
    # locations = aggregated towns, falling back to the single location until the
    # next pipeline run populates the aggregate
    agg = (df["locations"] if "locations" in df else blank).fillna("").astype(str)
    single = (df["location"] if "location" in df else blank).fillna("").astype(str)
    df["locations"] = agg.where(agg.str.strip() != "", single)
    # posted date (from the board) + when we first fetched it (new vs old)
    df["posted"] = (df["posted_date"] if "posted_date" in df else blank).fillna("").astype(str).str[:10]
    fs = (df["first_seen_at"] if "first_seen_at" in df else blank).fillna("").astype(str)
    df["fetched"] = fs.str[:16].str.replace("T", " ", regex=False)
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).replace(microsecond=0).isoformat()
    df["new"] = fs.ge(cutoff) & (fs != "")
    return df


@st.cache_data(ttl=60, show_spinner=False)
def load_runs(url: str) -> pd.DataFrame:
    return pd.DataFrame(get_store(url).recent_runs(40))


@st.cache_data(ttl=60, show_spinner=False)
def load_blobs(url: str) -> list:
    return get_store(url).tailored_blobs()


cfg = load_config()
url = get_db_url()

st.title("🧭 Job Search Assistant")
st.caption("Your automated UK data-science job hunt — discovery, fit-scoring, ATS CV tailoring and tracking.")

if not url:
    st.error("No `SUPABASE_DB_URL` found. Add it to your `.env` (local) or Streamlit secrets (cloud).")
    st.stop()

try:
    jobs = load_jobs(url)
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not connect to the database: {exc}")
    st.stop()

with st.sidebar:
    st.header("Job Search Assistant")
    st.metric("Jobs in database", len(jobs))
    if st.button("🔄 Refresh data", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Data refreshes every 60s, or click Refresh.")

tab_overview, tab_pipeline, tab_tracker, tab_board, tab_cvs = st.tabs(
    ["📊 Overview", "⚙️ Pipeline & LLMs", "✅ Live Jobs", "📋 Tracker", "📄 Tailored CVs"])

# ---------------------------------------------------------------- OVERVIEW
with tab_overview:
    if jobs.empty:
        st.info("No jobs yet. Run `python scripts/run_pipeline.py --mode first`, then refresh.")
    else:
        sc = {s: int((jobs["status"] == s).sum()) for s in STATUSES}
        c = st.columns(5)
        c[0].metric("Total", len(jobs))
        c[1].metric("⭐ Bucket-list", int(jobs["in_bucket"].sum()))
        c[2].metric("Shortlisted", sc["shortlisted"])
        c[3].metric("Tailored", sc["tailored"])
        c[4].metric("Applied", sc["applied"] + sc["interview"] + sc["offer"])

        left, right = st.columns(2)
        with left:
            st.subheader("By status")
            st.bar_chart(jobs["status"].value_counts())
        with right:
            st.subheader("By source")
            st.bar_chart(jobs["source"].value_counts())

        st.subheader("Fit-score distribution")
        scored = jobs[jobs["fit_score"] > 0]
        if scored.empty:
            st.caption("No fit scores yet (LLM scoring runs each pipeline pass).")
        else:
            bins = pd.cut(scored["fit_score"], [0, 50, 60, 70, 80, 90, 100],
                          labels=["<50", "50-59", "60-69", "70-79", "80-89", "90+"])
            st.bar_chart(bins.value_counts().sort_index())

        st.subheader("Top matches")
        top_cols = ["new", "title", "company", "locations", "fit", "in_bucket", "status", "posted", "fetched", "url"]
        top = jobs[jobs["fit_score"] >= 70].head(15)
        top = top[[c for c in top_cols if c in top.columns]]
        st.dataframe(top, hide_index=True, width="stretch",
                     column_config={"url": st.column_config.LinkColumn("link", display_text="open"),
                                    "in_bucket": st.column_config.CheckboxColumn("⭐"),
                                    "new": st.column_config.CheckboxColumn("🆕"),
                                    "locations": st.column_config.TextColumn("locations"),
                                    "posted": st.column_config.TextColumn("posted"),
                                    "fetched": st.column_config.TextColumn("fetched (UTC)"),
                                    "fit": st.column_config.NumberColumn("fit", format="%d")})

# ---------------------------------------------------------- PIPELINE & LLMs
with tab_pipeline:
    import json as _json

    llm = cfg.settings.get("llm", {})
    scoring = cfg.settings.get("scoring", {})
    sources = cfg.settings.get("sources", {})
    sec = cfg.secrets

    st.subheader("LLM routing (parallel models)")
    a, b, c = st.columns(3)
    a.metric("Fit-scoring", f"{llm.get('score_provider','gemini')} · {llm.get('score_model','')}")
    b.metric("CV tailoring", f"{llm.get('tailor_provider','gemini')} · {llm.get('tailor_model','')}")
    c.metric("Pacing", f"{llm.get('request_delay_seconds', 4)}s / call")
    a.metric("Score cap / run", f"{scoring.get('max_score_per_run', 40)} · batch {scoring.get('score_batch_size', 8)}")
    b.metric("Tailor cap / run", scoring.get("max_tailor_per_run", 6))
    c.metric("Tailor threshold", f"fit ≥ {scoring.get('tailor_threshold', 70)}")

    st.subheader("Connections")
    flags = {
        "Gemini": bool(sec.gemini_api_key), "DeepSeek": bool(sec.deepseek_api_key),
        "Groq": bool(sec.groq_api_key), "Reed": bool(sec.reed_api_key),
        "Adzuna": bool(sec.adzuna_app_id and sec.adzuna_app_key),
        "Supabase": bool(sec.supabase_db_url), "Telegram": bool(sec.telegram_bot_token),
    }
    cols = st.columns(len(flags))
    for col, (label, ok) in zip(cols, flags.items()):
        col.markdown(f"**{'🟢' if ok else '⚪'} {label}**")
    enabled = [k for k, v in sources.items() if isinstance(v, dict) and v.get("enabled")]
    st.caption("Active sources: " + (", ".join(enabled) or "none"))
    st.caption("ℹ️ This hosted app only needs Supabase (it reads the database). Your LLM and "
               "job-source keys live in GitHub Actions, where the pipeline actually runs — so the "
               "other lights being grey here is normal.")

    st.subheader("Run logs")
    st.caption("Every run, with where the jobs came from. Times are your local timezone.")
    runs = load_runs(url)
    if runs.empty:
        st.info("No runs logged yet. The pipeline records each run here automatically.")
    else:
        import datetime as _dt2

        def _local(ts):
            try:
                return _dt2.datetime.fromisoformat(str(ts)).astimezone().strftime("%Y-%m-%d %H:%M")
            except Exception:
                return str(ts)[:16].replace("T", " ")

        def _sj(r):
            raw = r.get("summary_json")
            if isinstance(raw, dict):
                return raw
            try:
                return _json.loads(raw) if raw else {}
            except Exception:
                return {}

        disp = []
        for _, r in runs.iterrows():
            sj = _sj(r)
            sc = {s.get("source", "?"): s.get("count", 0) for s in sj.get("sources", [])}
            google = next((v for k, v in sc.items() if "Google" in k), 0)
            ats = next((v for k, v in sc.items() if "ATS" in k), 0)
            gov = sum(v for k, v in sc.items() if "NHS" in k or "Civil Service" in k)
            disp.append({
                "run": _local(r.get("run_at")), "mode": r.get("mode"),
                "sector": sj.get("sector", "ALL"),
                "found": r.get("discovered"), "new": r.get("stored_new"),
                "Reed": sc.get("Reed", 0), "Adzuna": sc.get("Adzuna", 0), "Google": google,
                "ATS": ats, "Gov": gov,
                "🧭 DS": sj.get("category_data_science", 0), "DA": sj.get("category_data_analysis", 0),
                "🎯 companies": sj.get("companies_searched", 0), "⭐ bucket": sj.get("bucket_matches", 0),
                "scored": r.get("scored"), "tailored": r.get("tailored"),
                "note": r.get("llm_note") or (sj.get("telegram", "") or ""),
            })
        st.dataframe(pd.DataFrame(disp), hide_index=True, width="stretch", height=430)
        latest = _sj(runs.iloc[0])
        if latest:
            with st.expander("🔎 Latest run — full detail (every field)"):
                st.json(latest)
        st.line_chart(runs.set_index("run_at")[["discovered", "scored", "tailored"]].iloc[::-1])

# ----------------------------------------------------------------- LIVE JOBS
with tab_tracker:
    st.subheader("Live jobs")
    st.caption("Every job fetched from all sources. Add the ones you're pursuing from the **Tracker** tab.")
    if jobs.empty:
        st.info("No jobs yet.")
    else:
        f1, f2, f3, f4, f5 = st.columns([2, 2, 2, 1, 2])
        pick = f1.multiselect("Status", STATUSES, default=[])
        srcs = f2.multiselect("Source", sorted(jobs["source"].dropna().unique()))
        cats = f3.multiselect("Category", ["data-science", "data-analysis"])
        only_bucket = f4.checkbox("⭐ only")
        query = f5.text_input("Search title / company")
        min_fit = st.slider("Minimum fit score", 0, 100, 0, 5)

        view = jobs[~jobs["is_custom"]].copy()   # manual jobs live only in the Tracker
        if pick:
            view = view[view["status"].isin(pick)]
        if srcs:
            view = view[view["source"].isin(srcs)]
        if cats:
            view = view[view["category"].isin(cats)]
        if only_bucket:
            view = view[view["in_bucket"]]
        if query:
            q = query.lower()
            view = view[view["title"].str.lower().str.contains(q, na=False)
                        | view["company"].str.lower().str.contains(q, na=False)]
        view = view[view["fit_score"] >= min_fit].reset_index(drop=True)
        st.caption(f"Showing {len(view)} of {len(jobs)} jobs. Edit **status** and **notes**, then Save.")

        cols = ["new", "title", "company", "category", "locations", "posted", "fetched", "source",
                "in_bucket", "fit", "status", "notes", "url"]
        cols = [c for c in cols if c in view.columns]
        # editor key depends on the active filters so the grid re-renders cleanly when
        # they change (a fixed key kept stale edit-state and made filtering look broken)
        fkey = abs(hash(f"{sorted(pick)}|{sorted(srcs)}|{sorted(cats)}|{only_bucket}|{query}|{min_fit}"))
        edited = st.data_editor(
            view[cols], hide_index=True, width="stretch", num_rows="fixed",
            height=680, key=f"tracker_{fkey}",
            disabled=[c for c in cols if c not in ("status", "notes")],
            column_config={
                "status": st.column_config.SelectboxColumn("status", options=STATUSES, width="small"),
                "category": st.column_config.TextColumn("category", width="small"),
                "in_bucket": st.column_config.CheckboxColumn("⭐"),
                "new": st.column_config.CheckboxColumn("🆕"),
                "locations": st.column_config.TextColumn("locations", width="medium"),
                "posted": st.column_config.TextColumn("posted", width="small"),
                "fetched": st.column_config.TextColumn("fetched (UTC)", width="small"),
                "fit": st.column_config.NumberColumn("fit", format="%d", width="small"),
                "url": st.column_config.LinkColumn("link", display_text="open"),
                "notes": st.column_config.TextColumn("notes", width="large")})

        if st.button("💾 Save changes", type="primary"):
            store = get_store(url)
            keys = view["dedupe_key"].tolist()
            n = 0
            for i, key in enumerate(keys):
                new_status, new_notes = edited.iloc[i]["status"], str(edited.iloc[i]["notes"] or "")
                if new_status != view.iloc[i]["status"] or new_notes != str(view.iloc[i]["notes"] or ""):
                    store.set_status(key, new_status, notes=new_notes)
                    n += 1
            st.cache_data.clear()
            st.success(f"Saved {n} change(s).")
            st.rerun()

# --------------------------------------------------------------- TRACKER (KANBAN)
with tab_board:
    st.subheader("📋 Application tracker")
    st.caption("Move jobs through your pipeline — change a card's stage, tick 🗑 to delete, then **Save board**.")
    ca, cb = st.columns(2)
    with ca.expander("➕ Add a job manually"):
        with st.form("add_manual", clear_on_submit=True):
            m1, m2 = st.columns(2)
            _t = m1.text_input("Job title *")
            _c = m2.text_input("Company *")
            _u = st.text_input("Job URL")
            _l = st.text_input("Location", value="United Kingdom")
            _d = st.text_area("Job description", height=90)
            if st.form_submit_button("Add to tracker", type="primary"):
                if not _t or not _c:
                    st.warning("Title and company are required.")
                else:
                    get_store(url).add_custom_job(title=_t, company=_c, url=_u, location=_l, description=_d)
                    st.cache_data.clear()
                    st.success(f"Added '{_t}'.")
                    st.rerun()
    with cb.expander("📥 Add from fetched jobs (from Live Jobs)"):
        if jobs.empty:
            st.caption("No fetched jobs yet.")
        else:
            pool = jobs[(~jobs["tracked"]) & (~jobs["is_custom"])].sort_values("fit_score", ascending=False)
            labels = {f"{r['title']} · {r['company']} (fit {int(r.get('fit_score') or 0)})": r["dedupe_key"]
                      for _, r in pool.head(400).iterrows()}
            chosen = st.multiselect("Pick jobs to track", list(labels.keys()))
            if st.button("Add selected", type="primary", disabled=not chosen):
                get_store(url).set_tracked([labels[c] for c in chosen], True)
                st.cache_data.clear()
                st.success(f"Added {len(chosen)} job(s).")
                st.rerun()

    st.divider()
    tracked = jobs[jobs["tracked"]] if not jobs.empty else jobs.iloc[0:0]
    if tracked.empty:
        st.info("No jobs in your tracker yet — add some above (manually or from Live Jobs).")
    else:
        STAGES = [("📌 To apply", "#4f8cff", "shortlisted"),
                  ("✅ Applied", "#5ad19b", "applied"),
                  ("📝 Assessment", "#ffce6b", "assessment"),
                  ("✔️ Cleared", "#8ee06b", "assessment_cleared"),
                  ("🎤 Interview", "#c792ea", "interview"),
                  ("🏆 Offer", "#ffd54a", "offer"),
                  ("❌ Rejected", "#ff6b6b", "rejected")]
        STAGE_VALUES = [s for _, _, s in STAGES]
        APP = STAGE_VALUES[1:]     # everything past "To apply"
        moves: dict[str, str] = {}
        deletes: list[str] = []
        bcols = st.columns(len(STAGES))
        for bcol, (label, color, sval) in zip(bcols, STAGES):
            items = (tracked[~tracked["status"].isin(APP)] if sval == "shortlisted"
                     else tracked[tracked["status"] == sval]).sort_values("fit_score", ascending=False)
            bcol.markdown(
                f"<div style='background:{color};color:#0e1117;padding:6px 4px;border-radius:8px;"
                f"font-weight:700;text-align:center;font-size:12px'>{label}<br>{len(items)}</div>",
                unsafe_allow_html=True)
            for _, r in items.iterrows():
                with bcol.container(border=True):
                    star = "⭐ " if r.get("in_bucket") else ""
                    fit = int(r.get("fit_score") or 0)
                    link = f" · [open]({r['url']})" if r.get("url") else ""
                    st.markdown(
                        f"{star}**{str(r['title'])[:32]}**  \n"
                        f"<span style='color:#8b93a7;font-size:11px'>{str(r['company'])[:22]}"
                        f"{' · fit ' + str(fit) if fit else ''}</span>{link}", unsafe_allow_html=True)
                    mv = st.selectbox("stage", STAGE_VALUES, index=STAGE_VALUES.index(sval),
                                      key=f"mv_{r['dedupe_key']}", label_visibility="collapsed")
                    if mv != sval:
                        moves[r["dedupe_key"]] = mv
                    if st.checkbox("🗑", key=f"del_{r['dedupe_key']}"):
                        deletes.append(r["dedupe_key"])
        st.divider()
        if st.button("💾 Save board", type="primary"):
            store = get_store(url)
            n_moved = 0
            for k, s in moves.items():
                if k not in deletes:
                    store.set_status(k, s)
                    n_moved += 1
            if deletes:
                store.delete_jobs(deletes)
            st.cache_data.clear()
            st.success(f"Moved {n_moved} · deleted {len(deletes)}.")
            st.rerun()

# --------------------------------------------------------------- TAILORED CVS
with tab_cvs:
    st.subheader("Tailored CVs and cover letters")
    _docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    blob_map = {b["dedupe_key"]: b for b in load_blobs(url)}
    cvp = (jobs["cv_path"] if "cv_path" in jobs else pd.Series([""] * len(jobs), index=jobs.index)).fillna("").astype(str)
    tailored = jobs[(jobs["status"] == "tailored") | (cvp != "")] if not jobs.empty else jobs
    if jobs.empty or tailored.empty:
        st.info("No tailored CVs yet. The pipeline tailors the highest-fit jobs each run.")
    else:
        ready = sum(1 for k in tailored["dedupe_key"] if k in blob_map)
        st.caption(f"{len(tailored)} tailored · {ready} downloadable now. Older CVs (made before file-storage) "
                   "regenerate over the next runs; each run tailors only CVs it hasn't saved yet, never all of them.")
        for _, r in tailored.sort_values("fit_score", ascending=False).iterrows():
            star = "⭐ " if r.get("in_bucket") else ""
            with st.expander(f"{star}{r['title']} · {r['company']}  —  fit {int(r.get('fit_score') or 0)}"):
                if r.get("fit_reasoning"):
                    st.write(r["fit_reasoning"])
                b = blob_map.get(r["dedupe_key"])
                base = "".join(c for c in f"{r['company']}_{r['title']}" if c.isalnum() or c in " _-").strip().replace(" ", "_")[:60]
                if b:
                    dcols = st.columns(2)
                    for col, blob, label, suffix in (
                        (dcols[0], b.get("cv_blob"), "⬇️ CV (.docx)", "CV"),
                        (dcols[1], b.get("cover_blob"), "⬇️ Cover letter (.docx)", "CoverLetter")):
                        if blob:
                            col.download_button(label, bytes(blob), file_name=f"{base}_{suffix}.docx",
                                                key=f"{r['dedupe_key']}{suffix}", mime=_docx)
                        else:
                            col.caption(f"{suffix}: not available")
                else:
                    st.caption("⏳ File will be available after the next tailoring run "
                               "(this CV was made before file-storage; it's being regenerated once).")

