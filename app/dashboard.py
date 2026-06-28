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

STATUSES = ["new", "scored", "shortlisted", "tailored", "applied", "interview", "offer", "rejected"]

st.set_page_config(page_title="uk-jobops", page_icon="🧭", layout="wide")


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
    df["notes"] = df["notes"].fillna("")
    df["fit_score"] = df["fit_score"].fillna(0).astype(int)
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

st.title("🧭 uk-jobops")
st.caption("Automated UK data-science job discovery, fit-scoring and ATS CV tailoring.")

if not url:
    st.error("No `SUPABASE_DB_URL` found. Add it to your `.env` (local) or Streamlit secrets (cloud).")
    st.stop()

try:
    jobs = load_jobs(url)
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not connect to the database: {exc}")
    st.stop()

with st.sidebar:
    st.header("uk-jobops")
    st.metric("Jobs in database", len(jobs))
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Data refreshes every 60s, or click Refresh.")

tab_overview, tab_pipeline, tab_tracker, tab_cvs, tab_add = st.tabs(
    ["📊 Overview", "⚙️ Pipeline & LLMs", "✅ Job tracker", "📄 Tailored CVs", "➕ Add a job"])

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
        top_cols = ["new", "title", "company", "locations", "fit_score", "in_bucket", "status", "posted", "fetched", "url"]
        top = jobs[jobs["fit_score"] >= 70].head(15)
        top = top[[c for c in top_cols if c in top.columns]]
        st.dataframe(top, hide_index=True, use_container_width=True,
                     column_config={"url": st.column_config.LinkColumn("link", display_text="open"),
                                    "in_bucket": st.column_config.CheckboxColumn("⭐"),
                                    "new": st.column_config.CheckboxColumn("🆕"),
                                    "locations": st.column_config.TextColumn("locations"),
                                    "posted": st.column_config.TextColumn("posted"),
                                    "fetched": st.column_config.TextColumn("fetched (UTC)"),
                                    "fit_score": st.column_config.NumberColumn("fit", format="%d")})

# ---------------------------------------------------------- PIPELINE & LLMs
with tab_pipeline:
    import json as _json
    import subprocess
    import sys as _sys

    st.subheader("Run the pipeline")
    rc1, rc2 = st.columns([1, 3])
    mode = rc1.selectbox("Mode", ["recurring", "first"],
                         help="'first' backfills 2 weeks; 'recurring' looks at the last day")
    if rc2.button("▶️ Run pipeline now", type="primary"):
        with st.status("Running pipeline (this can take a few minutes)...", expanded=True) as status:
            try:
                proc = subprocess.run([_sys.executable, "scripts/run_pipeline.py", "--mode", mode],
                                      cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
                st.code((proc.stdout or "")[-4000:] or (proc.stderr or "")[-2000:] or "(no output)")
                status.update(label="Pipeline finished", state="complete")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Run failed: {exc}")
                status.update(label="Run failed", state="error")
        st.cache_data.clear()
    last_run = ROOT / "output" / "last_run.json"
    if last_run.exists():
        with st.expander("Last run summary"):
            st.json(_json.loads(last_run.read_text(encoding="utf-8")))
    st.divider()

    llm = cfg.settings.get("llm", {})
    scoring = cfg.settings.get("scoring", {})
    sources = cfg.settings.get("sources", {})
    sec = cfg.secrets

    st.subheader("LLM configuration (free tiers)")
    a, b, c = st.columns(3)
    a.metric("Primary model", f"{llm.get('primary','gemini')} · {llm.get('gemini_model','')}")
    b.metric("Critic", llm.get("critic") or "off (saves ~50% calls)")
    c.metric("Pacing", f"{llm.get('request_delay_seconds', 4)}s / call")
    a.metric("Score cap / run", scoring.get("max_score_per_run", 40))
    b.metric("Tailor cap / run", scoring.get("max_tailor_per_run", 6))
    c.metric("Tailor threshold", f"fit ≥ {scoring.get('tailor_threshold', 70)}")

    st.subheader("Connections")
    flags = {
        "Gemini key": bool(sec.gemini_api_key), "Groq key": bool(sec.groq_api_key),
        "Reed key": bool(sec.reed_api_key), "Adzuna key": bool(sec.adzuna_app_id and sec.adzuna_app_key),
        "Supabase DB": bool(sec.supabase_db_url), "Telegram": bool(sec.telegram_bot_token),
    }
    cols = st.columns(len(flags))
    for col, (label, ok) in zip(cols, flags.items()):
        col.markdown(f"**{'🟢' if ok else '⚪'} {label}**")
    enabled = [k for k, v in sources.items() if isinstance(v, dict) and v.get("enabled")]
    st.caption("Active sources: " + (", ".join(enabled) or "none"))

    st.subheader("Recent pipeline runs")
    runs = load_runs(url)
    if runs.empty:
        st.info("No runs logged yet. The pipeline records each run here automatically.")
    else:
        show = runs[["run_at", "mode", "discovered", "targets", "scored", "tailored", "stored_new", "llm_note"]]
        st.dataframe(show, hide_index=True, use_container_width=True)
        st.line_chart(runs.set_index("run_at")[["discovered", "targets", "scored"]].iloc[::-1])

# ----------------------------------------------------------------- TRACKER
with tab_tracker:
    st.subheader("Editable job tracker")
    if jobs.empty:
        st.info("No jobs yet.")
    else:
        f1, f2, f3, f4 = st.columns([2, 2, 1, 2])
        pick = f1.multiselect("Status", STATUSES, default=[])
        srcs = f2.multiselect("Source", sorted(jobs["source"].dropna().unique()))
        only_bucket = f3.checkbox("⭐ only")
        query = f4.text_input("Search title / company")
        min_fit = st.slider("Minimum fit score", 0, 100, 0, 5)

        view = jobs.copy()
        if pick:
            view = view[view["status"].isin(pick)]
        if srcs:
            view = view[view["source"].isin(srcs)]
        if only_bucket:
            view = view[view["in_bucket"]]
        if query:
            q = query.lower()
            view = view[view["title"].str.lower().str.contains(q, na=False)
                        | view["company"].str.lower().str.contains(q, na=False)]
        view = view[view["fit_score"] >= min_fit].reset_index(drop=True)
        st.caption(f"{len(view)} jobs. Edit **status** and **notes**, then Save.")

        cols = ["new", "title", "company", "locations", "posted", "fetched", "source",
                "in_bucket", "fit_score", "status", "notes", "url"]
        cols = [c for c in cols if c in view.columns]
        edited = st.data_editor(
            view[cols], hide_index=True, use_container_width=True, num_rows="fixed", key="tracker",
            disabled=[c for c in cols if c not in ("status", "notes")],
            column_config={
                "status": st.column_config.SelectboxColumn("status", options=STATUSES, width="small"),
                "in_bucket": st.column_config.CheckboxColumn("⭐"),
                "new": st.column_config.CheckboxColumn("🆕"),
                "locations": st.column_config.TextColumn("locations", width="medium"),
                "posted": st.column_config.TextColumn("posted", width="small"),
                "fetched": st.column_config.TextColumn("fetched (UTC)", width="small"),
                "fit_score": st.column_config.NumberColumn("fit", format="%d", width="small"),
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

# ----------------------------------------------------------------- ADD A JOB
with tab_add:
    st.subheader("Add a job manually")
    st.caption("For roles you found yourself. It enters the tailoring queue immediately.")
    with st.form("add_job", clear_on_submit=True):
        title = st.text_input("Job title *")
        company = st.text_input("Company *")
        urlin = st.text_input("Job URL")
        loc = st.text_input("Location", value="United Kingdom")
        desc = st.text_area("Job description (paste for best tailoring)", height=200)
        if st.form_submit_button("Add job", type="primary"):
            if not title or not company:
                st.warning("Title and company are required.")
            else:
                get_store(url).add_custom_job(title=title, company=company, url=urlin,
                                              location=loc, description=desc)
                st.cache_data.clear()
                st.success(f"Added '{title}' at {company}. See it in the Job tracker tab.")
