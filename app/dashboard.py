"""uk-jobops dashboard - jobs, pipeline runs, LLM config and an editable tracker,
all backed by the same Supabase database the pipeline writes to.

Run locally:   streamlit run app/dashboard.py
Deploy free:   Streamlit Community Cloud, with SUPABASE_DB_URL in app secrets.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import altair as alt
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

def _iframe(src, height=620, scrolling=True):
    """Embed a URL. Uses st.iframe (current API); falls back on older Streamlit versions."""
    try:
        return st.iframe(src, height=height, scrolling=scrolling)
    except Exception:
        import streamlit.components.v1 as _c
        return _c.iframe(src, height=height, scrolling=scrolling)

def get_store(url: str) -> Store:
    s = Store(url)
    s.init_schema()
    return s


@st.cache_resource(show_spinner=False)
def get_tracker(url: str):
    """Isolated application-tracker store (separate `applications` table; never touched by the pipeline)."""
    from uk_jobops.tracker import Tracker
    t = Tracker(url)
    t.init_schema()
    return t


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
                       ("category", ""), ("sector", ""), ("notes", ""), ("source", ""), ("status", "new"),
                       ("url", ""), ("cv_path", ""), ("fit_reasoning", "")):
        if _col not in df.columns:
            df[_col] = _def
    df["tracked"] = df["tracked"].fillna(False).astype(bool)
    df["is_custom"] = df["is_custom"].fillna(False).astype(bool)
    df["notes"] = df["notes"].fillna("")
    df["fit_score"] = df["fit_score"].fillna(0).astype(int)
    df["fit"] = df["fit_score"].where(df["status"] != "new")  # blank for not-yet-scored jobs
    # derive category for rows stored before the category column existed (backfilled server-side
    # on the next run; done client-side here so the DS/DA tabs work immediately)
    try:
        from uk_jobops.filtering import job_category
        _cat = df["category"].fillna("").astype(str)
        df["category"] = _cat.where(_cat.str.strip() != "", df["title"].fillna("").map(job_category))
    except Exception:
        pass
    # backfill sector for rows stored before the sector column (from the Master List)
    try:
        from uk_jobops.bucketlist import company_sector, load_company_sectors
        _smap = load_company_sectors("data/companies_master.csv")
        _sec = df["sector"].fillna("").astype(str)
        df["sector"] = _sec.where(_sec.str.strip() != "",
                                  df["company"].fillna("").map(lambda c: company_sector(c, _smap)))
    except Exception:
        pass
    df["sector"] = df["sector"].fillna("").replace("", "— other —")
    # locations = aggregated towns, falling back to the single location until the
    # next pipeline run populates the aggregate
    agg = (df["locations"] if "locations" in df else blank).fillna("").astype(str)
    single = (df["location"] if "location" in df else blank).fillna("").astype(str)
    df["locations"] = agg.where(agg.str.strip() != "", single)
    # posted date (from the board) + when we first fetched it (new vs old)
    df["posted"] = (df["posted_date"] if "posted_date" in df else blank).fillna("").astype(str).str[:10]
    fs = (df["first_seen_at"] if "first_seen_at" in df else blank).fillna("").astype(str)
    # stored UTC -> show UK local time (Europe/London) so 'fetched' reads as present time
    _ts = pd.to_datetime(fs, utc=True, errors="coerce")
    try:
        _ts = _ts.dt.tz_convert("Europe/London")
    except Exception:
        pass
    df["fetched"] = _ts.dt.strftime("%Y-%m-%d %H:%M").fillna("")
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).replace(microsecond=0).isoformat()
    df["new"] = fs.ge(cutoff) & (fs != "")
    return df


@st.cache_data(ttl=60, show_spinner=False)
def load_runs(url: str) -> pd.DataFrame:
    return pd.DataFrame(get_store(url).recent_runs(500))   # all past runs for Search Coverage inspection


@st.cache_data(ttl=60, show_spinner=False)
def load_recs(url: str) -> list:
    return get_store(url).recommendations_list()


cfg = load_config()
url = get_db_url()

# Bridge Streamlit secrets -> env so the Google service-account sync (gsync) works on Streamlit Cloud.
import os as _os
for _gk in ("GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_CALENDAR_ID", "GOOGLE_DRIVE_FOLDER_ID"):
    try:
        if not _os.environ.get(_gk) and _gk in st.secrets:
            _os.environ[_gk] = str(st.secrets[_gk])
    except Exception:
        pass

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
    _n_fetched = int((~jobs["is_custom"]).sum()) if not jobs.empty and "is_custom" in jobs else len(jobs)
    _n_manual = len(jobs) - _n_fetched
    st.metric("Jobs in database", _n_fetched,
              help=f"{_n_manual} manually-added job(s) live only in the Tracker" if _n_manual else None)
    if st.button("🔄 Refresh data", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Data refreshes every 60s, or click Refresh.")

def bar(series, *, scheme="tableau20", horizontal=False, height=300, sort="-y"):
    """Colour-coded Altair bar chart with the exact value printed on each bar."""
    df = (series.rename_axis("k").reset_index(name="v") if hasattr(series, "rename_axis")
          else pd.DataFrame({"k": list(series.keys()), "v": list(series.values())}))
    df["k"] = df["k"].astype(str)
    cat, val = alt.X("k:N", sort=sort, title=None, axis=alt.Axis(labelAngle=-30)), alt.Y("v:Q", title=None)
    enc = dict(y=cat, x=alt.X("v:Q", title=None)) if horizontal else dict(x=cat, y=val)
    base = alt.Chart(df).encode(**enc)
    bars = base.mark_bar(cornerRadius=3).encode(
        color=alt.Color("k:N", legend=None, scale=alt.Scale(scheme=scheme)),
        tooltip=["k", "v"])
    txt = base.mark_text(color="white", fontWeight="bold",
                         **({"align": "left", "dx": 4} if horizontal else {"dy": -7})).encode(text="v:Q")
    return (bars + txt).properties(height=height, width="container").configure_view(strokeWidth=0)


def show_bar(series, **kw):
    """Render the coloured Altair bar chart; fall back to a native bar chart if Altair errors
    (Streamlit/Altair version drift) so a chart NEVER degrades into a raw k/v table."""
    try:
        st.altair_chart(bar(series, **kw))
    except Exception:  # noqa: BLE001
        st.bar_chart(series)


def _priority(row) -> str:
    """Colour-coded importance from fit score + scoring status."""
    f = int(row.get("fit_score") or 0)
    if row.get("status") == "new" or f <= 0:
        return "▫️"
    if f >= 85:
        return "🟢"
    if f >= 75:
        return "🔵"
    if f >= 65:
        return "🟡"
    return "⚪"


def render_jobs(jobs, url, key_prefix, category=None, title="Jobs", caption=""):
    """Shared READ-ONLY jobs browser with filters and priority colours. Status is managed only
    in the Tracker tab. `category` limits to 'data-science' / 'data-analysis'."""
    st.subheader(title)
    if caption:
        st.caption(caption)
    if jobs.empty:
        st.info("No jobs yet. Run the pipeline, then refresh.")
        return
    base = jobs[~jobs["is_custom"]].copy()          # manual jobs live only in the Tracker
    if category:
        base = base[base["category"] == category]
    if base.empty:
        st.info("No jobs in this view yet.")
        return
    # Excel-style filters — every control below combines with AND (stack as many as you like).
    f1, f2, f3, f4 = st.columns([2, 2, 1, 3])
    srcs = f1.multiselect("Source", sorted(base["source"].dropna().unique()), key=f"{key_prefix}_src")
    secs = f2.multiselect("Sector", sorted(base["sector"].dropna().unique()), key=f"{key_prefix}_sec")
    only_bucket = f3.checkbox("⭐ top list", key=f"{key_prefix}_bk",
                              help="Show ONLY your bucket-list top companies (hide the '— other —' broad-market minors)")
    q = f4.text_input("Search title / company", key=f"{key_prefix}_q")
    g1, g2, g3, g4 = st.columns([2, 2, 2, 2])
    cat_opts = sorted([c for c in base["category"].dropna().unique() if c])
    cat_pick = g1.multiselect("Category", cat_opts, key=f"{key_prefix}_cat") if not category else []
    stat_pick = g2.multiselect("Status", sorted([s for s in base["status"].dropna().unique() if s]),
                               key=f"{key_prefix}_stat")
    co = g3.text_input("Company contains", key=f"{key_prefix}_co")
    sort_by = g4.selectbox("Sort by", ["Newest first", "Oldest first", "Fit (high→low)", "Fit (low→high)"],
                           key=f"{key_prefix}_sort")
    h1, h2 = st.columns([3, 2])
    fit_lo, fit_hi = h1.slider("Fit score range", 0, 100, (0, 100), 5, key=f"{key_prefix}_fitrange")
    page_size = h2.selectbox("Rows per page", [25, 50, 100, 200, 500], index=1, key=f"{key_prefix}_ps")

    view = base
    if srcs:
        view = view[view["source"].isin(srcs)]
    if secs:
        view = view[view["sector"].isin(secs)]
    if only_bucket:
        view = view[view["in_bucket"]]
    if cat_pick:
        view = view[view["category"].isin(cat_pick)]
    if stat_pick:
        view = view[view["status"].isin(stat_pick)]
    if co:
        view = view[view["company"].str.lower().str.contains(co.lower(), na=False)]
    if q:
        ql = q.lower()
        view = view[view["title"].str.lower().str.contains(ql, na=False)
                    | view["company"].str.lower().str.contains(ql, na=False)]
    view = view[(view["fit_score"] >= fit_lo) & (view["fit_score"] <= fit_hi)]
    # sort — default newest→oldest by the date we fetched it
    _datecol = "fetched" if "fetched" in view.columns else "first_seen_at"
    if sort_by == "Newest first":
        view = view.sort_values(_datecol, ascending=False)
    elif sort_by == "Oldest first":
        view = view.sort_values(_datecol, ascending=True)
    elif sort_by == "Fit (low→high)":
        view = view.sort_values("fit_score", ascending=True)
    else:
        view = view.sort_values(["fit_score", "in_bucket"], ascending=False)
    view = view.reset_index(drop=True)
    view["▲"] = view.apply(_priority, axis=1)
    # date-wise PAGES (latest first by default)
    total = len(view)
    n_pages = max(1, (total + page_size - 1) // page_size)
    page = 1
    if n_pages > 1:
        page = st.number_input(f"Page (1–{n_pages})", 1, n_pages, 1, 1, key=f"{key_prefix}_pg")
    start = (int(page) - 1) * page_size
    page_view = view.iloc[start:start + page_size]
    st.caption(f"Showing {len(page_view)} of {total} jobs · page {int(page)}/{n_pages}.  "
               "🟢 ≥85 · 🔵 75-84 · 🟡 65-74 · ⚪ <65 · ▫️ unscored · ⭐ target company. "
               "Filters stack (AND); sorted newest→oldest by default. Track jobs from the Tracker tab.")
    cols = ["▲", "new", "title", "company", "category", "sector", "locations", "posted", "fetched",
            "source", "in_bucket", "fit", "url"]
    if category:
        cols.remove("category")
    cols = [c for c in cols if c in view.columns]
    _tbl_h = min(max(len(page_view), 8) * 35 + 40, 1200)
    st.dataframe(
        page_view[cols], hide_index=True, width="stretch", height=_tbl_h,
        column_config={
            "▲": st.column_config.TextColumn("▲", width="small",
                                             help="Priority: 🟢≥85 🔵75+ 🟡65+ ⚪<65 ▫️unscored"),
            "category": st.column_config.TextColumn("category", width="small"),
            "sector": st.column_config.TextColumn("sector", width="small"),
            "in_bucket": st.column_config.CheckboxColumn("⭐"),
            "new": st.column_config.CheckboxColumn("🆕"),
            "locations": st.column_config.TextColumn("locations", width="medium"),
            "posted": st.column_config.TextColumn("posted", width="small"),
            "fetched": st.column_config.TextColumn("fetched (UK)", width="small"),
            "fit": st.column_config.NumberColumn("fit", format="%d", width="small"),
            "url": st.column_config.LinkColumn("link", display_text="open")})


def render_kanban(tracked, url, kp):
    """One kanban board for a set of tracked jobs. `kp` scopes widget keys so the All / DS / DA
    boards don't collide (a job can appear in All and its category board)."""
    if tracked.empty:
        st.info("No tracked jobs in this view.")
        return
    STAGES = [("📌 To apply", "#4f8cff", "shortlisted"), ("✅ Applied", "#5ad19b", "applied"),
              ("📝 Assessment", "#ffce6b", "assessment"), ("✔️ Cleared", "#8ee06b", "assessment_cleared"),
              ("🎤 Interview", "#c792ea", "interview"), ("🏆 Offer", "#ffd54a", "offer"),
              ("❌ Rejected", "#ff6b6b", "rejected")]
    STAGE_VALUES = [s for _, _, s in STAGES]
    APP = STAGE_VALUES[1:]
    moves: dict[str, str] = {}
    deletes: list[str] = []
    bcols = st.columns(len(STAGES))
    for bcol, (label, color, sval) in zip(bcols, STAGES):
        items = (tracked[~tracked["status"].isin(APP)] if sval == "shortlisted"
                 else tracked[tracked["status"] == sval]).sort_values("fit_score", ascending=False)
        bcol.markdown(f"<div style='background:{color};color:#0e1117;padding:6px 4px;border-radius:8px;"
                      f"font-weight:700;text-align:center;font-size:12px'>{label}<br>{len(items)}</div>",
                      unsafe_allow_html=True)
        for _, r in items.iterrows():
            with bcol.container(border=True):
                star = "⭐ " if r.get("in_bucket") else ""
                fit = int(r.get("fit_score") or 0)
                ttl = re.sub(r"[*_`\[\]<>]", "", str(r["title"]))[:60]
                co = re.sub(r"[*_`\[\]<>]", "", str(r["company"]))[:40]
                st.markdown(f"{star}**{ttl}**")
                st.caption(co + (f" · fit {fit}" if fit else ""))
                if r.get("url"):
                    st.markdown(f"[open job]({r['url']})")
                mv = st.selectbox("stage", STAGE_VALUES, index=STAGE_VALUES.index(sval),
                                  key=f"mv_{kp}_{r['dedupe_key']}", label_visibility="collapsed")
                if mv != sval:
                    moves[r["dedupe_key"]] = mv
                if st.checkbox("🗑 delete", key=f"del_{kp}_{r['dedupe_key']}"):
                    deletes.append(r["dedupe_key"])
    if st.button("💾 Save board", type="primary", key=f"save_{kp}"):
        store = get_store(url)
        n = 0
        for k, s in moves.items():
            if k not in deletes:
                store.set_status(k, s)
                n += 1
        if deletes:
            store.delete_jobs(deletes)
        st.cache_data.clear()
        st.success(f"Moved {n} · deleted {len(deletes)}.")
        st.rerun()


(tab_overview, tab_jobs, tab_intl, tab_source, tab_pipeline, tab_coverage, tab_bucket, tab_board,
 tab_apps, tab_apply, tab_autoapply, tab_cvs) = st.tabs(
    ["📊 Overview", "💼 Jobs", "🌍 International", "🗂️ By Source", "⚙️ Runs & LLMs", "🔎 Search Coverage",
     "🏢 Bucket List", "📌 Shortlist", "✅ My Applications", "🚀 Apply Queue", "🤖 Auto-Apply",
     "📝 Recommendations"])

# ---------------------------------------------------------------- OVERVIEW
with tab_overview:
    if jobs.empty:
        st.info("No jobs yet. Run `python scripts/run_pipeline.py --mode first`, then refresh.")
    else:
        ov = jobs[~jobs["is_custom"]]        # manual jobs live only in the Tracker
        sc = {s: int((ov["status"] == s).sum()) for s in STATUSES}
        c = st.columns(5)
        c[0].metric("Total", len(ov))
        c[1].metric("⭐ Bucket-list", int(ov["in_bucket"].sum()))
        c[2].metric("Shortlisted", sc["shortlisted"])
        c[3].metric("Tailored", sc["tailored"])
        c[4].metric("Applied", sc["applied"] + sc["interview"] + sc["offer"])
        _ds = int((ov["category"] == "data-science").sum())
        _ai = int((ov["category"] == "ai-engineer").sum())
        _da = int((ov["category"] == "data-analysis").sum())
        st.caption(f"🔬 Data Science: **{_ds}**  ·  🤖 AI Engineer: **{_ai}**  ·  📈 Data Analysis: **{_da}**  ·  "
                   f"🟢 strong (≥85): **{int((ov['fit_score'] >= 85).sum())}**")

        left, right = st.columns(2)
        with left:
            st.subheader("By status")
            show_bar(ov["status"].value_counts(), scheme="tableau10")
        with right:
            st.subheader("By source")
            show_bar(ov["source"].value_counts(), scheme="set2")

        st.subheader("By sector")
        st.caption("How many fetched jobs map to each of your 7 Master-List sectors "
                   "('— other —' = employers not on the Master List, e.g. broad Adzuna/Reed hits).")
        show_bar(ov["sector"].value_counts(), scheme="tableau20", horizontal=False, height=360, sort="-y")

        st.subheader("Fit-score distribution")
        scored = ov[ov["fit_score"] > 0]
        if scored.empty:
            st.caption("No fit scores yet (LLM scoring runs each pipeline pass).")
        else:
            bins = pd.cut(scored["fit_score"], [0, 50, 65, 75, 85, 100],
                          labels=["<50", "50-64", "65-74", "75-84", "85+"])
            show_bar(bins.value_counts().sort_index(), scheme="redyellowgreen", sort=None)

        st.subheader("Top matches")
        top_cols = ["new", "title", "company", "sector", "locations", "fit", "in_bucket", "status", "posted", "fetched", "url"]
        top = ov[ov["fit_score"] >= 65].sort_values("fit_score", ascending=False).head(40)
        top = top[[c for c in top_cols if c in top.columns]]
        st.dataframe(top, hide_index=True, width="stretch", height=min(max(len(top), 8) * 35 + 40, 1000),
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
    a.metric("Fit-scoring", f"{llm.get('score_provider','deepseek')} · {llm.get('score_model','')}")
    b.metric("Deep audit", f"{llm.get('tailor_provider','openai')} · {llm.get('tailor_model','')}")
    c.metric("Consensus verify", f"{llm.get('verify_provider','openai')} · {llm.get('verify_model','')}")
    a.metric("Score cap / run", f"{scoring.get('max_score_per_run', 40)} · batch {scoring.get('score_batch_size', 8)}")
    b.metric("Tailor cap / run", scoring.get("max_tailor_per_run", 6))
    c.metric("Tailor threshold", f"fit ≥ {scoring.get('tailor_threshold', 70)}")

    st.subheader("Connections")
    flags = {
        "Gemini": bool(sec.gemini_api_key), "OpenAI": bool(sec.openai_api_key),
        "DeepSeek": bool(sec.deepseek_api_key), "Bright Data": bool(sec.brightdata_api_key),
        "Reed": bool(sec.reed_api_key), "Adzuna": bool(sec.adzuna_app_id and sec.adzuna_app_key),
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
            google = next((v for k, v in sc.items() if "Bright Data" in k or "LinkedIn" in k or "Google" in k), 0)
            ats = next((v for k, v in sc.items() if "ATS" in k), 0)
            gov = sum(v for k, v in sc.items() if "NHS" in k or "Civil Service" in k)
            # how many ATS companies were found by FOLLOWING the careers page (the classifier)
            ats_classified = next((s.get("meta", {}).get("classified_from_careers", 0)
                                   for s in sj.get("sources", []) if "ATS" in s.get("source", "")), 0)
            disp.append({
                "run": _local(r.get("run_at")), "mode": r.get("mode"),
                "sector": sj.get("sector", "ALL"),
                "found": r.get("discovered"), "new": r.get("stored_new"),
                "Reed": sc.get("Reed", 0), "Adzuna": sc.get("Adzuna", 0), "LinkedIn": google,
                "ATS": ats, "ATS via careers": int(ats_classified or 0), "Gov": gov,
                "🧭 DS": sj.get("category_data_science", 0), "AI": sj.get("category_ai_engineer", 0),
                "DA": sj.get("category_data_analysis", 0),
                "🔎 searched": int(sj.get("companies_searched", 0) or 0),
                "of": str(sj.get("companies_in_sector", "") or ""),   # str: sector runs int, ALL runs "" (Arrow-safe)
                "co. w/roles": int(sj.get("companies_with_roles", 0) or 0),
                "⭐ bucket": int(sj.get("bucket_matches", 0) or 0),
                "scored": r.get("scored"), "tailored": r.get("tailored"),
                "src status": " · ".join(f"{s.get('source','?').split(' (')[0]}:{s.get('status','?')}"
                                         + (f"({s.get('count',0)})" if s.get('status') != 'ok' or not s.get('count') else "")
                                         for s in sj.get("sources", [])),
                "note": r.get("llm_note") or (sj.get("telegram", "") or ""),
            })
        st.dataframe(pd.DataFrame(disp), hide_index=True, width="stretch",
                     height=min(max(len(disp), 8) * 35 + 40, 1100))
        # coverage — pick ANY run (full OR sector, any day, including one-off sector runs like 'Banking')
        _sector_runs = [(_local(r.get("run_at")), _sj(r)) for _, r in runs.iterrows()]
        if _sector_runs:
            st.markdown("**🔎 Run coverage** — every run (full or single-sector), search results per run")
            _labels = [f"{t}  ·  {sj.get('sector', '?')}  ({sj.get('companies_searched', 0)}"
                       f"/{sj.get('companies_in_sector', '?')} searched)" for t, sj in _sector_runs]
            _pick = st.selectbox("Pick a run", _labels, key="cov_pick", label_visibility="collapsed")
            _sr = {lab: sj for lab, (_, sj) in zip(_labels, _sector_runs)}[_pick]
            with st.container(border=True):
                cc = st.columns(4)
                cc[0].metric("Companies searched",
                             f"{_sr.get('companies_searched', 0)}/{_sr.get('companies_in_sector', 0)}")
                cc[1].metric("With matching roles", _sr.get("companies_with_roles", 0))
                cc[2].metric("Not reached", _sr.get("companies_missed", 0))
                cc[3].metric("Roles found (kept)", _sr.get("targets", 0))
                _names = _sr.get("companies_with_roles_names") or []
                if _names:
                    st.caption(f"✅ Companies with roles ({len(_names)}): " + ", ".join(_names))
        latest = _sj(runs.iloc[0])
        if latest:
            with st.expander("🔎 Latest run — full detail (every field)"):
                st.json(latest)
        st.line_chart(runs.set_index("run_at")[["discovered", "scored", "tailored"]].iloc[::-1])

# ----------------------------------------------------------------- JOBS (All / DS / DA sub-tabs)
with tab_jobs:
    _view = st.radio("View", ["💼 All jobs", "🔬 Data Science", "🤖 AI Engineer", "📈 Data Analysis"],
                     horizontal=True, key="jobs_view", label_visibility="collapsed")
    if _view.startswith("🔬"):
        render_jobs(jobs, url, "jds", "data-science", "🔬 Data Science roles",
                    "Data Scientist, Applied / Decision / Research Scientist and similar.")
    elif _view.startswith("🤖"):
        render_jobs(jobs, url, "jai", "ai-engineer", "🤖 AI Engineer roles",
                    "AI Engineer, ML Engineer, LLM / GenAI, NLP / Computer Vision, MLOps and similar.")
    elif _view.startswith("📈"):
        render_jobs(jobs, url, "jda", "data-analysis", "📈 Data Analysis roles",
                    "Data Analyst, Analytics Engineer, BI / Insight / MI Analyst and similar (incl. civil service).")
    else:
        render_jobs(jobs, url, "jall", None, "💼 All jobs",
                    "Every job fetched from all sources, both categories. Filter by sector or source below.")

# ----------------------------------------------------------------- BY SOURCE
with tab_source:
    st.subheader("🗂️ Jobs by source")
    st.caption("What each search operation contributed. Times/counts update every run.")
    if jobs.empty:
        st.info("No jobs yet.")
    else:
        b = jobs[~jobs["is_custom"]].copy()
        agg = (b.groupby("source").agg(
                   jobs=("dedupe_key", "count"),
                   avg_fit=("fit_score", lambda s: round(s[s > 0].mean(), 1) if (s > 0).any() else 0),
                   bucket=("in_bucket", "sum"),
                   DS=("category", lambda s: int((s == "data-science").sum())),
                   AI=("category", lambda s: int((s == "ai-engineer").sum())),
                   DA=("category", lambda s: int((s == "data-analysis").sum())))
               .reset_index().sort_values("jobs", ascending=False))
        # combined 'Bright Data (all)' pillar = every board fetched via Bright Data SERP together
        _bd = b[b["source"].astype(str).str.contains("Bright Data", na=False)]
        if not _bd.empty:
            _bdrow = {"source": "🛰 Bright Data (all)", "jobs": len(_bd),
                      "avg_fit": round(_bd.loc[_bd["fit_score"] > 0, "fit_score"].mean(), 1)
                      if (_bd["fit_score"] > 0).any() else 0.0,
                      "bucket": int(_bd["in_bucket"].sum()),
                      "DS": int((_bd["category"] == "data-science").sum()),
                      "AI": int((_bd["category"] == "ai-engineer").sum()),
                      "DA": int((_bd["category"] == "data-analysis").sum())}
            agg = pd.concat([pd.DataFrame([_bdrow]), agg], ignore_index=True)
        st.dataframe(agg, hide_index=True, width="stretch",
                     height=min(max(len(agg), 4) * 35 + 40, 700),
                     column_config={"avg_fit": st.column_config.NumberColumn("avg fit", format="%.1f")})
        st.caption("'🛰 Bright Data (all)' is every board (LinkedIn + Indeed + Reed + Totaljobs + … via "
                   "Bright Data SERP) combined; the rows below it break it down per board. Use the **Source** "
                   "filter in the Jobs tabs to drill in.")

        # ---- 📆 per-day-by-source: how many jobs each source picked up on each date ----
        st.divider()
        st.markdown("**📆 Jobs picked up per day, by source**")
        import altair as alt
        bb = b.copy()
        bb["day"] = pd.to_datetime(bb.get("fetched"), errors="coerce").dt.strftime("%Y-%m-%d")
        bb = bb[bb["day"].notna()]
        if bb.empty:
            st.caption("No dated jobs yet.")
        else:
            per = bb.groupby(["day", "source"]).size().reset_index(name="jobs")
            chart = (alt.Chart(per).mark_bar().encode(
                x=alt.X("day:O", title=None, axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("jobs:Q", title="jobs"),
                color=alt.Color("source:N", title="source"),
                tooltip=["day", "source", "jobs"]).properties(height=340))
            st.altair_chart(chart, width="stretch")
            piv = (per.pivot(index="day", columns="source", values="jobs")
                   .fillna(0).astype(int).sort_index(ascending=False))
            st.caption("Date × source (newest first):")
            st.dataframe(piv, width="stretch")

# --------------------------------------------------------------- TRACKER (KANBAN)
with tab_board:
    st.subheader("📌 Shortlist — jobs the engine found that you're tracking")
    st.caption("Pipeline-discovered roles you've shortlisted. For logging jobs you've actually applied "
               "to, use the **✅ My Applications** tab (that's your durable, isolated tracker).")
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
    with cb.expander("📥 Add from fetched jobs (from the Jobs tab)"):
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
        st.info("No jobs in your tracker yet — add some above (manually or from the Jobs tab).")
    else:
        st.caption("Separate boards for close monitoring. Change a card's stage, tick **🗑 delete**, "
                   "then **Save board**.")
        _nds = int((tracked["category"] == "data-science").sum())
        _nai = int((tracked["category"] == "ai-engineer").sum())
        _nda = int((tracked["category"] == "data-analysis").sum())
        _board = st.radio("Board", [f"🗂 All ({len(tracked)})", f"🔬 Data Science ({_nds})",
                                    f"🤖 AI Engineer ({_nai})", f"📈 Data Analysis ({_nda})"],
                          horizontal=True, key="board_view", label_visibility="collapsed")
        if _board.startswith("🔬"):
            render_kanban(tracked[tracked["category"] == "data-science"], url, "ds")
        elif _board.startswith("🤖"):
            render_kanban(tracked[tracked["category"] == "ai-engineer"], url, "ai")
        elif _board.startswith("📈"):
            render_kanban(tracked[tracked["category"] == "data-analysis"], url, "da")
        else:
            render_kanban(tracked, url, "all")

# --------------------------------------------------------------- RECOMMENDATIONS
with tab_cvs:
    st.subheader("📝 CV keywords per job (+ deep tailoring for top picks)")
    st.caption("2.0: for EVERY job you get the exact ATS keywords to add to your CV for that role "
               "(green = you already evidence them, gaps = the role wants them but you don't). The full "
               "section-by-section audit + cover letter are generated for the highest-fit picks.")
    recs = load_recs(url)
    if not recs:
        st.info("No jobs yet. Keywords are written for every job each run (zero LLM cost); the deep audit "
                "is written for the highest-fit picks. Run the pipeline, then refresh.")
    else:
        cat_pick = st.multiselect("Category", ["data-science", "ai-engineer", "data-analysis"], key="rec_cat")
        rows = [r for r in recs if not cat_pick or r.get("category") in cat_pick]
        st.caption(f"{len(rows)} of {len(recs)} shown.")
        for r in rows:
            star = "⭐ " if r.get("in_bucket") else ""
            fit = int(r.get("fit_score") or 0)
            dot = "🟢" if fit >= 85 else "🔵" if fit >= 75 else "🟡" if fit >= 65 else "⚪"
            with st.expander(f"{dot} {star}{r['title']} · {r['company']}  —  fit {fit}"):
                if r.get("url"):
                    st.markdown(f"[Open job posting]({r['url']})")
                if r.get("cv_keywords"):
                    st.markdown(f"**🔑 {r['cv_keywords']}**")   # keyword-first (the 2.0 headline)
                t1, t2 = st.tabs(["🧩 CV recommendations", "✉️ Cover letter"])
                with t1:
                    st.markdown(r.get("recommendations")
                                or "_Keyword list above. Full section-by-section audit is generated for "
                                   "higher-fit picks._")
                with t2:
                    cover = r.get("cover_text") or ""
                    if cover:
                        st.text_area("Cover letter (copy)", cover, height=380,
                                     key=f"cov_{r['dedupe_key']}")
                    else:
                        st.caption("No cover letter text.")


# --------------------------------------------------------------- SEARCH COVERAGE
with tab_coverage:
    import json as _jc
    import datetime as _dtc

    def _loc(ts):
        try:
            return _dtc.datetime.fromisoformat(str(ts)).astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(ts)[:16].replace("T", " ")

    def _sjc(r):
        raw = r.get("summary_json")
        if isinstance(raw, dict):
            return raw
        try:
            return _jc.loads(raw) if raw else {}
        except Exception:
            return {}

    st.subheader("🔎 Search coverage — is every company being searched?")
    st.caption("Proof of what each run actually searched: how many of your target companies were "
               "queried, how many returned roles, and which sources contributed. Your daily 4 AM run "
               "searches the ENTIRE list; here you can confirm it every day.")
    runs = load_runs(url)
    if runs.empty:
        st.info("No runs logged yet.")
    else:
        allruns = [(_loc(r.get("run_at")), _sjc(r)) for _, r in runs.iterrows()]
        # ⚠️ PROMINENT SERP HEALTH BANNER — the #1 thing to watch. Shows the latest run's Google/SERP
        # note (which carries the exact first_error diagnostic) so a broken SERP is impossible to miss.
        _latest = allruns[0][1] if allruns else {}
        _serp = next((x for x in _latest.get("sources", []) if "Bright Data" in x.get("source", "")), None)
        if _serp:
            _smsg = _serp.get("message", "") or ""
            if _serp.get("status") == "error" or int(_serp.get("count", 0) or 0) == 0:
                st.error(f"🔴 **Google/SERP (Bright Data): {_serp.get('status')} — 0 jobs.**  {_smsg}")
                st.caption("Fix guide → **HTTP 401/403 / auth** = the BRIGHTDATA_API_KEY GitHub secret "
                           "doesn't match your Bright Data account (copy the key from Bright Data → "
                           "Settings → Users and API keys → Show). **'zone' / 404** = set "
                           "BRIGHTDATA_SERP_ZONE=serp_api1. **'got HTML not JSON'** = set the zone's "
                           "response format to Full JSON. Then re-run.")
            else:
                st.success(f"🟢 Google/SERP (Bright Data): {_smsg}")
            st.divider()

        # 2.0 RUN REPORT — the detailed per-run message (same content as Telegram), front and centre.
        latest_report = next((s.get("report") for _, s in allruns if s.get("report")), None)
        if latest_report:
            from uk_jobops.report import report_markdown
            with st.expander("📋 Latest run report — bucket list, every source, best jobs", expanded=True):
                st.markdown(report_markdown(latest_report))
            st.divider()
        # headline: the most recent full/all-company run
        full = [(t, s) for t, s in allruns if s.get("companies_in_sector")]
        if full:
            t, s = full[0]
            searched = int(s.get("companies_searched", 0) or 0)
            total = int(s.get("companies_in_sector", 0) or 0)
            withroles = int(s.get("companies_with_roles", 0) or 0)
            pct = round(100 * searched / total) if total else 0
            st.markdown(f"**Latest full run — {t}**")
            m = st.columns(4)
            m[0].metric("Companies searched", f"{searched}/{total}")
            m[1].metric("Coverage", f"{pct}%")
            m[2].metric("Returned roles", withroles)
            m[3].metric("Missed", max(0, total - searched))
            if pct < 100:
                st.warning(f"Only {pct}% of the list was searched on this run — some companies may have "
                           "been skipped (timeout/error). Re-run or check the pipeline if this persists.")
            else:
                st.success("Full list searched. ✅")
        else:
            st.info("No all-company run recorded yet (the daily 4 AM run populates this).")

        # SOURCE STATUS — latest run, front and centre, so you can SEE which sources ran/skipped/errored
        latest = allruns[0][1] if allruns else {}
        lsrcs = latest.get("sources", [])
        if lsrcs:
            st.divider()
            st.markdown("**🔌 Sources — latest run**  (🟢 ok · ⚪ skipped · 🔴 error)")
            srows = []
            for x in lsrcs:
                stt = str(x.get("status", ""))
                dot = "🟢" if stt == "ok" else ("🔴" if stt == "error" else "⚪")
                srows.append({" ": dot, "source": x.get("source", "?"), "status": stt,
                              "jobs": x.get("count", 0), "detail": (x.get("message") or "")[:300]})
            st.dataframe(pd.DataFrame(srows), hide_index=True, width="stretch")
            st.caption("'Google/SERP (Bright Data)' is the main engine (LinkedIn + Reed + Totaljobs + "
                       "CV-Library + Indeed via Google). The structured 'LinkedIn Jobs'/'Indeed' scrapers "
                       "stay **skipped** until the Bright Data Web Scraper product is activated.")

        # trend table + charts across all runs
        rows = []
        for t, s in allruns:
            sc = {x.get("source", "?").split(" (")[0]: x.get("count", 0) for x in s.get("sources", [])}
            rows.append({
                "run": t, "sector": s.get("sector", "ALL"),
                "discovered": int(s.get("discovered", 0) or 0), "new": int(s.get("stored_new", 0) or 0),
                "searched": int(s.get("companies_searched", 0) or 0),
                "with_roles": int(s.get("companies_with_roles", 0) or 0),
                "LinkedIn": next((v for k, v in sc.items() if "Bright" in k or "LinkedIn" in k), 0),
                "ATS": next((v for k, v in sc.items() if "ATS" in k), 0),
                "Reed": sc.get("Reed", 0), "Adzuna": sc.get("Adzuna", 0),
                "DS": int(s.get("category_data_science", 0) or 0),
                "AI": int(s.get("category_ai_engineer", 0) or 0),
                "DA": int(s.get("category_data_analysis", 0) or 0),
            })
        rdf = pd.DataFrame(rows)
        st.divider()
        cc = st.columns(2)
        with cc[0]:
            st.caption("Jobs discovered per run")
            st.bar_chart(rdf.set_index("run")["discovered"], height=240)
        with cc[1]:
            st.caption("Companies searched vs returned roles")
            st.line_chart(rdf.set_index("run")[["searched", "with_roles"]], height=240)
        cc2 = st.columns(2)
        with cc2[0]:
            st.caption("Jobs by source, per run")
            st.bar_chart(rdf.set_index("run")[["LinkedIn", "ATS", "Reed", "Adzuna"]], height=240)
        with cc2[1]:
            st.caption("Jobs by category, per run")
            st.bar_chart(rdf.set_index("run")[["DS", "AI", "DA"]], height=240)

        st.divider()
        st.markdown("**Inspect a run — which companies returned roles**")
        labels = [f"{i + 1}. {t}  ·  {s.get('sector', 'ALL')}" for i, (t, s) in enumerate(allruns)]
        pick = st.selectbox("Run", labels, key="coverage_run_pick")
        s = allruns[labels.index(pick)][1]
        d = st.columns(4)
        d[0].metric("Discovered", int(s.get("discovered", 0) or 0))
        d[1].metric("New", int(s.get("stored_new", 0) or 0))
        d[2].metric("Searched", int(s.get("companies_searched", 0) or 0))
        d[3].metric("With roles", int(s.get("companies_with_roles", 0) or 0))
        names = s.get("companies_with_roles_names", []) or []
        if names:
            st.caption(f"✅ Companies that returned matching roles this run ({len(names)}):")
            st.write(", ".join(sorted(names)))
        st.caption("Per-source detail:")
        st.dataframe(pd.DataFrame([{"source": x.get("source"), "status": x.get("status"),
                                    "jobs": x.get("count"), "note": (x.get("message") or "")[:120]}
                                   for x in s.get("sources", [])]), hide_index=True, width="stretch")
        st.dataframe(rdf, hide_index=True, width="stretch", height=300)


# --------------------------------------------------------------- BUCKET LIST
with tab_bucket:
    from uk_jobops.bucketlist import _core
    st.subheader("🏢 Bucket list — your target companies")
    st.caption("The curated list of top UK employers the engine searches every day (per-company). "
               "Green = has returned at least one job into the database.")
    bpath = cfg.path(cfg.settings.get("bucket_list", {}).get("path", "data/companies_master.csv"))
    try:
        bl = pd.read_csv(bpath).fillna("")
    except Exception as exc:
        bl = None
        st.error(f"Could not load the bucket list: {exc}")
    if bl is not None and not bl.empty:
        # which companies have jobs in the DB (normalised-name match)
        jobcores = set()
        if not jobs.empty and "company" in jobs:
            jobcores = {_core(c) for c in jobs["company"].astype(str) if c}
        bl["has_jobs"] = bl["company_name"].astype(str).map(lambda n: "🟢" if _core(n) in jobcores else "—")
        tier_col = bl["tier"] if "tier" in bl else pd.Series(["master"] * len(bl))
        m = st.columns(4)
        m[0].metric("Companies", len(bl))
        m[1].metric("Tier A (priority)", int((tier_col == "top100").sum()))
        m[2].metric("Sectors", bl["sector"].nunique())
        m[3].metric("With ≥1 job", int((bl["has_jobs"] == "🟢").sum()))
        st.caption("Companies per sector")
        st.bar_chart(bl["sector"].value_counts(), height=260)
        secs = ["All sectors"] + sorted(bl["sector"].unique().tolist())
        pick = st.selectbox("Filter by sector", secs, key="bucket_sec")
        view = bl if pick == "All sectors" else bl[bl["sector"] == pick]
        only_missing = st.checkbox("Show only companies with no jobs yet", key="bucket_missing")
        if only_missing:
            view = view[view["has_jobs"] != "🟢"]
        cols = [c for c in ["has_jobs", "company_name", "sector", "tier", "industry", "careers_url"] if c in view]
        st.caption(f"{len(view)} companies")
        st.dataframe(
            view[cols].rename(columns={"has_jobs": " ", "company_name": "company"}),
            hide_index=True, width="stretch", height=min(max(len(view), 8) * 35 + 40, 1200),
            column_config={"careers_url": st.column_config.LinkColumn("careers", display_text="open")})


# --------------------------------------------------------------- MY APPLICATIONS (isolated tracker)
with tab_apps:
    import datetime as _dta

    from uk_jobops.tracker import (STATUS_LABEL, STATUSES, day_name, ordered_stages, rows_to_csv,
                                   rows_to_ics, stage_label)

    def _rerun():
        try:
            st.rerun()
        except Exception:
            st.experimental_rerun()

    _COUNTRIES = ["United Kingdom", "Ireland", "Germany", "Netherlands", "France", "Spain", "Switzerland",
                  "Sweden", "Poland", "Portugal", "Italy", "Belgium", "Luxembourg", "Denmark",
                  "United States", "Canada", "Australia", "New Zealand", "UAE", "Singapore", "Malaysia",
                  "Russia", "India", "Other"]

    st.subheader("✅ My Applications — your personal tracker")
    st.caption("A private, durable log of every job you actually apply to — with the day, date and "
               "country. This lives in its OWN database table, completely separate from the search "
               "pipeline, so nothing the engine does can ever change or lose it.")

    trk = get_tracker(url)
    apps = trk.list_all()
    stages = ordered_stages(apps)          # 6 defaults + any custom stages you've created

    # ---- metrics ----
    from uk_jobops.tracker import board_stats
    stt = board_stats(apps)
    mc = st.columns(min(8, len(stages) + 1))
    mc[0].metric("Total", stt.get("total", 0))
    for i, s in enumerate(stages[:len(mc) - 1]):
        cnt = sum(1 for a in apps if (a.get("status") or "") == s)
        mc[i + 1].metric(stage_label(s), cnt)

    # ---- add a new application ----
    with st.expander("➕ Add an application", expanded=not apps):
        with st.form("add_app", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            company = c1.text_input("Company *")
            role = c2.text_input("Role title *")
            status = c3.selectbox("Stage", stages, format_func=stage_label)
            custom_stage = c3.text_input("…or new custom stage")
            c4, c5, c6 = st.columns(3)
            country_sel = c4.selectbox("Country", _COUNTRIES, index=0)
            country_other = c5.text_input("…or type country")
            city = c6.text_input("City")
            c7, c8, c9 = st.columns(3)
            applied = c7.date_input("Applied on", value=_dta.date.today())
            salary = c8.text_input("Salary (optional)")
            url_in = c9.text_input("Job link (optional)")
            c10, c11 = st.columns(2)
            next_action = c10.text_input("Next action (e.g. 'Coding test')")
            next_date = c11.date_input("Next action date", value=None)
            c12, c13 = st.columns(2)
            email_link = c12.text_input("📧 Email link (paste a Gmail link)")
            email_subject = c13.text_input("Email subject / note")
            notes = st.text_area("Notes", height=70)
            submitted = st.form_submit_button("Add application", type="primary")
            if submitted:
                if not company.strip() or not role.strip():
                    st.warning("Company and role title are required.")
                else:
                    final_stage = (custom_stage.strip().lower().replace(" ", "_")
                                   or status)   # a typed custom stage wins over the dropdown
                    trk.add(company=company.strip(), role_title=role.strip(),
                            country=(country_other.strip() or country_sel), city=city.strip(),
                            source_url=url_in.strip(), status=final_stage, applied_date=applied,
                            salary=salary.strip(), notes=notes.strip(),
                            next_action=next_action.strip(), next_action_date=(next_date or None),
                            email_link=email_link.strip(), email_subject=email_subject.strip())
                    st.success(f"Added {role.strip()} at {company.strip()}.")
                    _rerun()

    # ---- export / backup ----
    ec1, ec2, ec3 = st.columns([1, 1, 4])
    ec1.download_button("⬇️ CSV backup", rows_to_csv(apps), file_name="applications.csv",
                        mime="text/csv", disabled=not apps)
    ec2.download_button("📅 Calendar (.ics)", rows_to_ics(apps), file_name="applications.ics",
                        mime="text/calendar", disabled=not apps)
    ec3.caption("CSV → save to Google Drive · .ics → import into Google/Apple Calendar "
                "(interview & next-action dates become all-day events).")

    # ---- live Google sync (app-side service account) ----
    from uk_jobops import gsync
    if gsync.configured():
        g1, g2, g3 = st.columns([1.4, 1.4, 4])
        if g1.button("📅 Sync dates → Google Calendar", disabled=not apps):
            try:
                nsy, msg = gsync.sync_calendar(apps)
                st.success(f"Synced {nsy} calendar event(s). {msg}")
            except Exception as exc:
                st.error(f"Calendar sync failed: {str(exc)[:200]}")
        if g2.button("💾 Back up → Google Drive", disabled=not apps):
            try:
                st.success(f"Drive backup {gsync.backup_to_drive(rows_to_csv(apps))}.")
            except Exception as exc:
                st.error(f"Drive backup failed: {str(exc)[:200]}")
        g3.caption("Live sync via your Google service account (Calendar + Drive). The pipeline also "
                   "syncs automatically each day.")
    else:
        st.caption("🔌 To turn on live Google sync, add a service account "
                   "(GOOGLE_SERVICE_ACCOUNT_JSON + GOOGLE_CALENDAR_ID + GOOGLE_DRIVE_FOLDER_ID) to secrets. "
                   "Until then, use the .ics / CSV export above.")

    st.divider()

    # ---- kanban board (dynamic: default + custom stages) ----
    from urllib.parse import quote as _gq
    if not apps:
        st.info("No applications yet — add your first one above.")
    else:
        by = {s: [a for a in apps if (a.get("status") or "") == s] for s in stages}
        cols = st.columns(len(stages))
        for ci, s in enumerate(stages):
            with cols[ci]:
                st.markdown(f"**{stage_label(s)}**  ·  {len(by[s])}")
                for a in by[s]:
                    aid = a["id"]
                    with st.container(border=True):
                        st.markdown(f"**{a.get('company','')}**")
                        st.caption(a.get("role_title", ""))
                        loc = " · ".join(x for x in [a.get("country", ""), a.get("city", "")] if x)
                        when = str(a.get("applied_date") or "")[:10]
                        meta = " · ".join(x for x in [loc, (f"{day_name(when)} {when}" if when else "")] if x)
                        if meta:
                            st.caption(meta)
                        if a.get("next_action"):
                            nd = str(a.get("next_action_date") or "")[:10]
                            st.caption(f"⏭ {a['next_action']}" + (f" ({nd})" if nd else ""))
                        links = []
                        if a.get("source_url"):
                            links.append(f"[job]({a['source_url']})")
                        if a.get("email_link"):
                            links.append(f"[📧 email]({a['email_link']})")
                        else:
                            links.append(f"[🔍 find email](https://mail.google.com/mail/u/0/#search/"
                                         f"{_gq(a.get('company','') or '')})")
                        if links:
                            st.markdown(" · ".join(links))
                        # move between stages
                        i = stages.index(s)
                        b1, b2, b3 = st.columns(3)
                        if b1.button("◀", key=f"l{aid}", disabled=i == 0, help="Move back"):
                            trk.set_status(aid, stages[i - 1]); _rerun()
                        if b2.button("▶", key=f"r{aid}", disabled=i == len(stages) - 1, help="Advance"):
                            trk.set_status(aid, stages[i + 1]); _rerun()
                        with b3.popover("✎") if hasattr(st, "popover") else st.expander("✎ edit"):
                            mv = st.selectbox("Move to stage", stages, index=i, format_func=stage_label,
                                              key=f"mv{aid}")
                            cst = st.text_input("…or new custom stage", key=f"cs{aid}")
                            ne = st.text_input("Next action", a.get("next_action", ""), key=f"na{aid}")
                            nde = st.date_input("Next date", value=None, key=f"nd{aid}")
                            el = st.text_input("📧 Email link", a.get("email_link", ""), key=f"el{aid}")
                            nt = st.text_area("Notes", a.get("notes", ""), key=f"nt{aid}", height=70)
                            if st.button("Save", key=f"sv{aid}"):
                                new_stage = cst.strip().lower().replace(" ", "_") or mv
                                trk.update(aid, status=new_stage, next_action=ne, notes=nt,
                                           email_link=el.strip(), next_action_date=(nde or None))
                                _rerun()
                            if st.button("🗑 Delete", key=f"dl{aid}"):
                                trk.delete(aid); _rerun()

    # ---- CALENDAR DASHBOARD: applications per day ----
    if apps:
        st.divider()
        st.subheader("📆 Applications calendar — how many you applied to each day")
        import altair as alt
        dts = [str(a.get("applied_date"))[:10] for a in apps if a.get("applied_date")]
        s = pd.to_datetime(pd.Series(dts), errors="coerce").dropna()
        if len(s):
            daily = s.dt.normalize().value_counts()
            today = pd.Timestamp.today().normalize()
            start = min(s.min().normalize(), today - pd.Timedelta(days=120))
            full = pd.date_range(start, max(s.max().normalize(), today), freq="D")
            dfc = pd.DataFrame({"date": full})
            dfc["count"] = dfc["date"].map(lambda d: int(daily.get(d, 0)))
            dfc["week"] = dfc["date"].dt.strftime("%G-W%V")
            dfc["dow"] = dfc["date"].dt.strftime("%a")
            km = st.columns(4)
            km[0].metric("Applied this week", int(dfc[dfc["date"] >= today - pd.Timedelta(days=today.dayofweek)]["count"].sum()))
            km[1].metric("Applied this month", int(dfc[dfc["date"].dt.month.eq(today.month) & dfc["date"].dt.year.eq(today.year)]["count"].sum()))
            km[2].metric("Active days", int((dfc["count"] > 0).sum()))
            km[3].metric("Best day", int(dfc["count"].max()))
            heat = (alt.Chart(dfc).mark_rect(cornerRadius=2, stroke="white", strokeWidth=1).encode(
                x=alt.X("week:O", title=None, axis=alt.Axis(labelAngle=-90, labelFontSize=9)),
                y=alt.Y("dow:O", title=None, sort=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]),
                color=alt.Color("count:Q", title="apps",
                                scale=alt.Scale(scheme="greens", domainMin=0), legend=alt.Legend(orient="right")),
                tooltip=[alt.Tooltip("date:T", title="date"), alt.Tooltip("count:Q", title="applications")])
                .properties(height=200))
            st.altair_chart(heat, width="stretch")
            st.caption("Applications per day (last ~4 months). Darker = more applications that day.")
            recent = dfc[dfc["date"] >= today - pd.Timedelta(days=60)].set_index("date")["count"]
            st.bar_chart(recent, height=200)


# --------------------------------------------------------------- INTERNATIONAL (visa sponsors only)
with tab_intl:
    st.subheader("🌍 International — visa-sponsoring roles only")
    st.caption("Non-UK roles that explicitly offer, or are LIKELY to offer, visa sponsorship (strict "
               "rule — everything else is dropped at ingest). Priority: 🇪🇺 EU first, then rest of world.")
    _EUP1 = {"Ireland", "Germany", "Netherlands", "Austria", "Denmark", "France", "Sweden", "Finland",
             "Norway", "Switzerland", "Luxembourg", "Belgium"}
    if jobs.empty:
        st.info("No jobs yet.")
    else:
        ij = jobs[~jobs["is_custom"]].copy()
        if "country" not in ij.columns:
            ij["country"] = ""
        ij["country"] = ij["country"].fillna("")
        ij = ij[~ij["country"].isin(["United Kingdom", "Unknown", ""])]
        if ij.empty:
            st.info("No international sponsor roles stored yet. They appear once the pipeline runs with "
                    "global search on — the strict filter keeps only sponsors, so this stays clean.")
        else:
            ij["tier"] = ij["country"].map(lambda c: "🇪🇺 EU (priority 1)"
                                           if c in _EUP1 else "🌐 Rest of world (priority 2)")
            for _c in ("visa_sponsorship", "geo_score"):
                if _c not in ij.columns:
                    ij[_c] = "" if _c == "visa_sponsorship" else 0
            mc = st.columns(4)
            mc[0].metric("Total (sponsors)", len(ij))
            mc[1].metric("🇪🇺 EU (p1)", int(ij["tier"].str.startswith("🇪🇺").sum()))
            mc[2].metric("🌐 Rest (p2)", int((~ij["tier"].str.startswith("🇪🇺")).sum()))
            mc[3].metric("Countries", ij["country"].nunique())
            cca, ccb = st.columns(2)
            with cca:
                st.caption("Jobs by country")
                st.bar_chart(ij["country"].value_counts(), height=280)
            with ccb:
                st.caption("Sponsor signal")
                st.bar_chart(ij["visa_sponsorship"].replace("", "unknown").value_counts(), height=280)
            f1, f2 = st.columns(2)
            _tier_pick = f1.multiselect("Priority", sorted(ij["tier"].unique()), key="intl_tier")
            _ctry_pick = f2.multiselect("Country", sorted(ij["country"].unique()), key="intl_ctry")
            v = ij
            if _tier_pick:
                v = v[v["tier"].isin(_tier_pick)]
            if _ctry_pick:
                v = v[v["country"].isin(_ctry_pick)]
            v = v.sort_values(["tier", "geo_score", "fit_score"], ascending=[True, False, False])
            cols = [c for c in ["country", "title", "company", "category", "visa_sponsorship", "fit",
                                "locations", "source", "posted", "url"] if c in v.columns]
            st.caption(f"{len(v)} international sponsor roles (EU first).")
            st.dataframe(v[cols], hide_index=True, width="stretch",
                         height=min(max(len(v), 8) * 35 + 40, 1100),
                         column_config={"url": st.column_config.LinkColumn("link", display_text="open"),
                                        "visa_sponsorship": st.column_config.TextColumn("visa"),
                                        "fit": st.column_config.NumberColumn("fit", format="%d")})


# --------------------------------------------------------------- APPLY QUEUE (Phase 1)
with tab_apply:
    import datetime as _dtq

    from uk_jobops.cv_match import CV_FILES, CV_LABEL

    def _rerun_q():
        try:
            st.rerun()
        except Exception:
            st.experimental_rerun()

    _acfg = cfg.settings.get("apply", {})
    _athr = int(_acfg.get("threshold", 95))
    st.subheader(f"🚀 Apply Queue — direct-employer roles scored ≥ {_athr}")
    st.caption("High-fit roles on company ATS sites (Greenhouse/Lever/Ashby/Workday/… — never Indeed/"
               "LinkedIn). Each shows the best-fit CV to attach and a ready answer sheet. Mark applied "
               "when done (it logs to My Applications).")
    try:
        queue = get_store(url).apply_queue(min_score=_athr, limit=100)
    except Exception as exc:
        queue = []
        st.error(f"Could not load the queue: {str(exc)[:160]}")

    # answer sheet from the base CV / profile (constant across applications)
    _cv = cfg.base_cv
    _ct = _cv.get("contact", {})
    _answers = {
        "Full name": _cv.get("name", "Manoj Ram Mopati"),
        "Email": _ct.get("email", ""), "Phone": _ct.get("phone", ""),
        "Location": _ct.get("location", "London, UK"), "LinkedIn": _ct.get("linkedin", ""),
        "GitHub": _ct.get("github", ""), "Portfolio": _ct.get("portfolio", ""),
        "Right to work": "Yes — UK Graduate visa (can work now, no sponsorship needed in the UK)",
        "Require visa sponsorship (UK)": "No",
        "Years of experience": "4+", "Notice period": "Immediate / 1 week",
        "Willing to relocate": "Yes (UK; internationally with sponsorship)",
    }

    if not queue:
        st.info("Nothing in the queue yet. Jobs appear here once the pipeline scores a direct-ATS role "
                f"at ≥ {_athr}. Lower `apply.threshold` in settings to widen it.")
    else:
        st.success(f"{len(queue)} role(s) ready to apply.")
        st.markdown("**Standard answer sheet** (same for every application — copy fields as needed):")
        st.dataframe(pd.DataFrame([{"field": k, "value": v} for k, v in _answers.items()]),
                     hide_index=True, width="stretch", height=min(len(_answers) * 35 + 40, 500))
        st.divider()
        for a in queue:
            key = a.get("matched_cv") or ""
            cv_file = CV_FILES.get(key, "")
            cv_label = CV_LABEL.get(key, key or "—")
            fit = int(a.get("fit_score") or 0)
            with st.expander(f"🟢 {fit} · {a.get('title','')} — {a.get('company','')}  ·  CV: {cv_label}",
                             expanded=False):
                meta = " · ".join(x for x in [a.get("country", ""), a.get("location", ""),
                                              a.get("category", ""), a.get("visa_sponsorship", "")] if x)
                if meta:
                    st.caption(meta)
                c1, c2, c3 = st.columns([2, 2, 2])
                if a.get("url"):
                    c1.markdown(f"### [→ Open & apply]({a['url']})")
                # download the matched CV to attach
                try:
                    _p = cfg.path(f"{_acfg.get('cv_dir','data/cvs')}/{cv_file}")
                    if cv_file and _p.exists():
                        c2.download_button(f"📎 {cv_label} CV", _p.read_bytes(), file_name=cv_file,
                                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                           key=f"cv_{a['dedupe_key']}")
                    else:
                        c2.caption(f"CV file missing: {cv_file}")
                except Exception as exc:
                    c2.caption(f"CV load error: {str(exc)[:60]}")
                if c3.button("✅ Mark applied", key=f"ap_{a['dedupe_key']}"):
                    try:
                        get_tracker(url).add(company=a.get("company", ""), role_title=a.get("title", ""),
                                             country=a.get("country", "United Kingdom"),
                                             source_url=a.get("url", ""), status="applied",
                                             applied_date=_dtq.date.today(),
                                             notes=f"CV: {cv_label}")
                        get_store(url).set_status(a["dedupe_key"], "applied")
                        st.success("Logged to My Applications.")
                        _rerun_q()
                    except Exception as exc:
                        st.error(f"Could not mark applied: {str(exc)[:120]}")
                if a.get("description"):
                    with st.popover("🎯 ATS keywords") if hasattr(st, "popover") else st.expander("🎯 ATS keywords"):
                        from uk_jobops.keywords import candidate_skills, extract_keywords
                        _kw = extract_keywords(a.get("description", ""), candidate_skills(cfg.profile, cfg.base_cv))
                        st.markdown(_kw.to_line() or "_no keywords_")

    # ---- Apply Activity: every auto-apply, with its confirmation screenshot ----
    st.divider()
    st.subheader("📸 Apply Activity")
    st.caption("Every application the agent submitted, with the confirmation screenshot (also sent to "
               "Telegram at the time). Written by scripts/auto_apply.py.")
    try:
        log = get_store(url).apply_log_rows(limit=200)
    except Exception as exc:
        log = []
        st.caption(f"(activity log unavailable: {str(exc)[:100]})")
    if not log:
        st.info("No applications logged yet. Run `python scripts/auto_apply.py` on your Mac; each "
                "submitted application is recorded here with its screenshot.")
    else:
        st.metric("Applications submitted", len(log))
        for r in log[:60]:
            when = str(r.get("applied_at") or "")[:16].replace("T", " ")
            with st.expander(f"✅ {r.get('company','')} — {r.get('role_title','')}  ·  {when}  ·  CV: {r.get('cv','')}"):
                if r.get("url"):
                    st.markdown(f"[open job posting]({r['url']})")
                if r.get("has_shot"):
                    try:
                        img = get_store(url).apply_screenshot(int(r["id"]))
                        if img:
                            st.image(img, caption="confirmation screenshot", width="stretch")
                    except Exception:
                        st.caption("(screenshot could not be loaded)")


# ================================================================= AUTO-APPLY
# Three steps, nothing else: 1) choose  2) run & watch  3) status.
with tab_autoapply:

    def _rr():
        try:
            st.rerun()
        except Exception:
            st.experimental_rerun()

    def _gh():
        """(token, repo) for triggering workflows."""
        tok = ""
        for k in ("GITHUB_TOKEN", "GH_TOKEN"):
            try:
                tok = tok or str(st.secrets.get(k, "") or _os.environ.get(k, ""))
            except Exception:
                tok = tok or _os.environ.get(k, "")
        try:
            repo = str(st.secrets.get("GITHUB_REPO", "") or _os.environ.get("GITHUB_REPO", ""))
        except Exception:
            repo = _os.environ.get("GITHUB_REPO", "")
        return tok, (repo or "ManojRam7/MLLM_AI_Career_Assistant")

    def _dispatch(wf, inputs):
        import requests as _rq
        tok, repo = _gh()
        if not tok:
            return False, "Add GITHUB_TOKEN to Streamlit secrets (see Setup below)."
        try:
            r = _rq.post(f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/dispatches",
                         headers={"Authorization": f"Bearer {tok}",
                                  "Accept": "application/vnd.github+json"},
                         json={"ref": "main",
                               "inputs": {k: str(v).lower() if isinstance(v, bool) else str(v)
                                          for k, v in inputs.items()}}, timeout=25)
            if r.status_code == 204:
                return True, f"https://github.com/{repo}/actions"
            if r.status_code == 401:
                return False, "GitHub rejected the token (401) — it's expired or mistyped."
            return False, f"HTTP {r.status_code}: {r.text[:140]}"
        except Exception as exc:
            return False, str(exc)[:140]

    _tok, _repo = _gh()
    try:
        S = get_store(url)
    except Exception as _e:
        S = None
        st.error(f"Database unreachable: {str(_e)[:140]}")

    st.subheader("🤖 Auto-Apply")
    st.caption("Queue the jobs you want, then watch a real browser fill them in the cloud.")

    if S is not None:
        # ---------------------------------------------------------------- STEP 1 · choose
        st.markdown("### 1 · Choose what to apply to")
        c1, c2 = st.columns([3, 1])
        thr = c1.slider("Minimum fit score", 0, 100,
                        int(cfg.settings.get("apply", {}).get("threshold", 75)), 5, key="aa_thr")
        if c2.button("🔄 Refresh", width="stretch", key="aa_refresh"):
            st.cache_data.clear(); _rr()

        try:
            scored = S.apply_queue(min_score=thr, limit=100)
        except Exception as _e:
            scored, _ = [], st.warning(f"Couldn't load jobs: {str(_e)[:120]}")
        opts = {f"{int(r.get('fit_score') or 0)} · {(r.get('title') or '')[:46]} — "
                f"{(r.get('company') or '')[:22]}": r for r in scored}
        picked = st.multiselect(f"Jobs the agent can fill end-to-end ({len(scored)} at ≥{thr})",
                                list(opts), key="aa_pick")
        if not scored:
            st.info("Nothing here yet. Lower the score, run a search (bottom of this page), or paste a "
                    "URL below. Note: Workday / iCIMS / Taleo are excluded — they need a login, so use "
                    "the Chrome extension for those.")

        pasted = [u.strip() for u in (st.text_area(
            "…or paste application URLs (one per line)", height=90, key="aa_paste",
            placeholder="https://jobs.lever.co/…\nhttps://boards.greenhouse.io/…") or "").splitlines()
            if u.strip().lower().startswith("http")]

        submit_batch = st.toggle("Auto-submit after filling", value=False, key="aa_submit",
                                 help="OFF = fill only, you press Submit yourself while watching.")
        n_sel = len(picked) + len(pasted)
        if st.button(f"➕ Queue {n_sel} job(s)", type="primary", width="stretch",
                     disabled=(n_sel == 0), key="aa_queue"):
            items = [{"url": opts[l].get("url", ""), "title": opts[l].get("title", ""),
                      "company": opts[l].get("company", ""), "source": "queue",
                      "auto_submit": submit_batch} for l in picked]
            items += [{"url": u, "source": "pasted", "auto_submit": submit_batch} for u in pasted]
            try:
                st.success(f"Queued {S.add_apply_requests(items)} job(s)."); _rr()
            except Exception as _e:
                st.error(str(_e)[:160])

        try:
            n_q = len(S.apply_requests("queued", limit=200))
        except Exception:
            n_q = 0

        # ---------------------------------------------------------------- STEP 2 · run & watch
        st.divider()
        st.markdown("### 2 · Run it & watch live")
        st.caption(f"{n_q} job(s) waiting in the queue.")

        cs = st.text_input("Your Codespace name or URL", key="aa_cs",
                           value=str(st.session_state.get("aa_cs_saved", "")),
                           placeholder="e.g. glorious-space-guide-abc123",
                           help="Create one from the button below, then paste its name here once.")
        cs_name = (cs or "").strip().replace("https://", "").split(".")[0].replace("-6080", "")
        if cs_name:
            st.session_state["aa_cs_saved"] = cs_name
        # noVNC params: connect straight away, scale to fit, and reconnect if the run restarts —
        # so the viewer shows the desktop instead of a "Connect" button.
        watch_url = (f"https://{cs_name}-6080.app.github.dev/vnc.html"
                     "?autoconnect=1&resize=scale&reconnect=1&show_dot=1") if cs_name else ""

        if not cs_name:
            st.link_button("🖥️ Create your Codespace (one time)",
                           f"https://github.com/codespaces/new?repo={_repo}&ref=main", width="stretch")
            st.caption("Create it once, copy its name from the URL, paste it above — then this page can "
                       "show the live browser inline every time.")
        else:
            b1, b2 = st.columns(2)
            if b1.button("▶ Run & watch live", type="primary", width="stretch",
                         disabled=(n_q == 0), key="aa_run_live"):
                st.success(f"{n_q} job(s) queued — your Codespace picks them up within ~15s. "
                           "Watch it happen below.")
            b2.link_button("Open Codespace", "https://github.com/codespaces", width="stretch")
            st.caption("Your Codespace runs a watcher that applies to anything you queue, automatically. "
                       "If it's not running, open the Codespace terminal and start it:")
            with st.expander("Start the watcher manually"):
                st.code("./scripts/queue_watcher.sh", language="bash")
                st.caption("Or apply once, right now:  `./scripts/watch_apply.sh --from-queue`")
            st.markdown("**Live browser**")
            _iframe(watch_url, height=640, scrolling=True)
            st.caption("No password needed — it connects automatically. If it stays blank, check port "
                       "**6080** is **Public** in the Codespace PORTS tab, and that the Codespace is "
                       "still running (they stop after ~30 min idle).")

        with st.expander("No Codespace? Run it headless in GitHub Actions instead"):
            lim = st.number_input("How many this run", 1, 50, min(10, max(1, n_q or 10)), key="aa_lim")
            if st.button("Run headless (no live view)", width="stretch",
                         disabled=(n_q == 0 or not _tok), key="aa_run_headless"):
                ok, msg = _dispatch("apply.yml", {"limit": int(lim), "source": "queued-urls",
                                                  "submit": bool(submit_batch), "min_score": int(thr)})
                st.success(f"Started — [watch the log]({msg})") if ok else st.error(msg)
            st.caption("Fills and screenshots everything, but you can't watch it happen.")

        # ---------------------------------------------------------------- STEP 3 · status
        st.divider()
        st.markdown("### 3 · What happened")
        try:
            rows = S.apply_requests_rows(limit=100)
        except Exception:
            rows = []
        if not rows:
            st.info("Nothing queued yet.")
        else:
            import pandas as _pd
            EMO = {"queued": "⏳ queued", "processing": "⚙️ running", "done": "✅ submitted",
                   "needs_submit": "📝 filled — submit it", "needs_manual": "🔒 CAPTCHA — finish it",
                   "dead_link": "🔗 dead link", "error": "❌ error", "skipped": "⏭️ skipped"}
            df = _pd.DataFrame(rows)
            df["status"] = df["status"].map(lambda s: EMO.get(s, s))
            st.dataframe(df[["status", "title", "company", "url", "note"]], hide_index=True,
                         width="stretch", height=min(len(df) * 35 + 40, 360))
            k1, k2 = st.columns(2)
            if k1.button("🧹 Clear finished", width="stretch", key="aa_cf"):
                S.clear_apply_requests("done"); _rr()
            if k2.button("🗑️ Clear all", width="stretch", key="aa_ca"):
                S.clear_apply_requests("all"); _rr()

        with st.expander("🧠 What the agent answered (per field)"):
            try:
                runs = S.apply_event_runs(limit=10)
            except Exception:
                runs = []
            if not runs:
                st.caption("Nothing yet — run something first.")
            else:
                rl = {f"{str(r['started_at'])[:16]} · {r['jobs']} job(s)": r["run_id"] for r in runs}
                rid = rl[st.selectbox("Run", list(rl), key="aa_ev_run")]
                for e in (S.apply_events(rid) or []):
                    if e["kind"] == "job_start":
                        st.markdown(f"**📄 {e.get('role_title') or 'Application'} — {e.get('company','')}** "
                                    f"· CV `{e.get('answer')}`")
                    elif e["kind"] == "field":
                        st.markdown(f"{'✅' if e.get('ok') else '⚠️'} **{e.get('field','')}** → "
                                    f"{e.get('answer','')}  ·  "
                                    f"*{'AI' if e.get('origin') == 'ai' else 'profile'}*")
                    elif e["kind"] == "job_end":
                        st.markdown(f"🏁 {e.get('field','')} → `{e.get('answer','')}`")

        with st.expander("📸 Screenshots of each step"):
            try:
                shots = S.apply_step_runs(limit=20)
            except Exception:
                shots = []
            if not shots:
                st.caption("No screenshots yet.")
            else:
                sl = {f"{str(r.get('finished_at'))[:16]} · {(r.get('role_title') or '')[:38]} — "
                      f"{(r.get('company') or '')[:20]}": r for r in shots}
                sr = sl[st.selectbox("Application", list(sl), key="aa_shot_pick")]
                steps = S.apply_steps(sr["url"], sr.get("run_id") or "")
                if steps:
                    i = st.radio("Stage", range(len(steps)), horizontal=True, key="aa_shot_stage",
                                 format_func=lambda i: steps[i].get("label") or f"step {i+1}")
                    stp = steps[int(i)]
                    if stp.get("note"):
                        st.caption(stp["note"])
                    if stp.get("has_shot"):
                        img = S.apply_step_shot(int(stp["id"]))
                        if img:
                            st.image(img, width="stretch")

        # ---------------------------------------------------------------- extras (tucked away)
        st.divider()
        with st.expander("🔎 Run a job search (find more roles)"):
            SRC = {"Company ATS boards": "ats", "Adzuna": "adzuna", "Reed": "reed",
                   "Bright Data / Google": "brightdata"}
            sp = st.multiselect("Sources", list(SRC), default=["Company ATS boards"], key="aa_src")
            s1, s2 = st.columns(2)
            sf = s1.slider("Alert threshold", 0, 100, 75, 5, key="aa_sfit")
            sc = s2.selectbox("Scope", ["full", "all", "auto"], key="aa_scope")
            if st.button("Run search in the cloud", width="stretch",
                         disabled=(not sp or not _tok), key="aa_rs"):
                ok, msg = _dispatch("search.yml", {"sources": ",".join(SRC[s] for s in sp),
                                                   "sector": sc, "min_fit": int(sf), "mode": "recurring"})
                st.success(f"Search started — [watch]({msg})") if ok else st.error(msg)

        with st.expander("⚙️ Setup (one time)"):
            st.markdown(
                f"**GitHub token** (only needed for the headless Actions runs)\n\n"
                f"Settings → Developer settings → Fine-grained tokens → repo `{_repo}` → "
                "Permissions → Actions: **Read and write**. Then in Streamlit secrets:")
            st.code(f'GITHUB_TOKEN = "github_pat_..."\nGITHUB_REPO  = "{_repo}"', language="toml")
            st.markdown("**Codespace** — create once from the button in step 2, then in its PORTS tab "
                        "right-click port **6080** → Port Visibility → **Public** so the viewer can be "
                        "embedded here. It's protected by the VNC password (`vscode`).")
            st.markdown("**Login-walled sites** (Workday, iCIMS, LinkedIn) can't be automated — use the "
                        "Chrome extension in `extension/` for those: one click, fills instantly.")


