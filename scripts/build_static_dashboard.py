"""Render a self-contained, tabbed static dashboard to site/index.html from Supabase,
and write tailored CV/cover .docx files into site/cvs/ so they're downloadable.
The Actions workflow publishes site/ to GitHub Pages after every run (read-only).

Tabs (Overview / Jobs / Tracker / Tailored / Pipeline) are client-side JS - no server
needed, which is why GitHub Pages can host it. Editing the tracker, adding jobs and
triggering runs live only in the local Streamlit app."""
from __future__ import annotations

import datetime as dt
import html
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from uk_jobops.config import load_config  # noqa: E402

OUT = pathlib.Path("site")


def _gather() -> dict:
    cfg = load_config()
    from uk_jobops.db import Store

    store = Store(cfg.secrets.supabase_db_url)
    store.init_schema()
    data = {
        "jobs": store.all_jobs(2000),
        "runs": store.recent_runs(40),
        "status": store.status_counts(),
        "source": store.source_counts(),
        "blobs": store.tailored_blobs(),
        "llm": cfg.settings.get("llm", {}),
        "scoring": cfg.settings.get("scoring", {}),
    }
    store.close()
    return data


def _esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _-]+", "", s or "").strip().replace(" ", "_")[:70]


def _write_cvs(blobs: list[dict], outdir: pathlib.Path) -> dict:
    """Write each tailored .docx into site/cvs/ and return {dedupe_key: {cv,cover}} URLs."""
    m: dict[str, dict] = {}
    if not blobs:
        return m
    outdir.mkdir(parents=True, exist_ok=True)
    for r in blobs:
        key = r.get("dedupe_key", "")
        base = (_safe(f"{r.get('company','')}_{r.get('title','')}") or "cv") + "_" + key[:6]
        entry = {}
        if r.get("cv_blob"):
            fn = f"{base}_CV.docx"
            (outdir / fn).write_bytes(bytes(r["cv_blob"]))
            entry["cv"] = f"cvs/{fn}"
        if r.get("cover_blob"):
            fn = f"{base}_CoverLetter.docx"
            (outdir / fn).write_bytes(bytes(r["cover_blob"]))
            entry["cover"] = f"cvs/{fn}"
        if entry:
            m[key] = entry
    return m


def _kpi(label: str, value) -> str:
    return f'<div class="kpi"><div class="n">{_esc(value)}</div><div class="l">{_esc(label)}</div></div>'


def _bars(counts: dict) -> str:
    if not counts:
        return '<p class="muted">No data yet.</p>'
    top = max(counts.values()) or 1
    return "".join(
        f'<div class="bar"><span class="bl">{_esc(k)}</span>'
        f'<span class="bt"><i style="width:{int(v / top * 100)}%"></i></span><span class="bv">{v}</span></div>'
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))


def _fit_bars(jobs: list[dict]) -> str:
    b = {"90+": 0, "80-89": 0, "70-79": 0, "60-69": 0, "50-59": 0, "<50": 0}
    for j in jobs:
        f = int(j.get("fit_score") or 0)
        if f <= 0:
            continue
        b[("90+" if f >= 90 else "80-89" if f >= 80 else "70-79" if f >= 70
           else "60-69" if f >= 60 else "50-59" if f >= 50 else "<50")] += 1
    if not any(b.values()):
        return '<p class="muted">No fit scores yet.</p>'
    top = max(b.values()) or 1
    return "".join(
        f'<div class="bar"><span class="bl">{k}</span>'
        f'<span class="bt"><i style="width:{int(v / top * 100)}%"></i></span><span class="bv">{v}</span></div>'
        for k, v in b.items())


def _fitcls(f: int) -> str:
    return "fit hi" if f >= 80 else "fit mid" if f >= 70 else "fit"


def _top_table(jobs: list[dict]) -> str:
    top = sorted(jobs, key=lambda j: -(int(j.get("fit_score") or 0)))[:15]
    if not top:
        return '<p class="muted">No scored jobs yet.</p>'
    rows = []
    for j in top:
        f = int(j.get("fit_score") or 0)
        link = f'<a href="{_esc(j.get("url"))}" target="_blank" rel="noopener">open</a>' if j.get("url") else ""
        rows.append(f"<tr><td>{_esc(j.get('title'))}</td><td>{_esc(j.get('company'))}</td>"
                    f"<td class='star'>{'★' if j.get('in_bucket') else ''}</td><td class='{_fitcls(f)}'>{f or ''}</td>"
                    f"<td>{_esc(j.get('status'))}</td><td>{link}</td></tr>")
    return ("<table><thead><tr><th>Title</th><th>Company</th><th>⭐</th><th>Fit</th>"
            f"<th>Status</th><th></th></tr></thead><tbody>{''.join(rows)}</tbody></table>")


def _kanban(jobs: list[dict]) -> str:
    cols = [("shortlisted", "Shortlisted"), ("tailored", "Tailored"), ("applied", "Applied"),
            ("interview", "Interview"), ("offer", "Offer"), ("rejected", "Rejected")]
    out = []
    for key, label in cols:
        items = sorted([j for j in jobs if j.get("status") == key], key=lambda j: -(int(j.get("fit_score") or 0)))
        cards = "".join(
            f'<div class="kjob"><b>{_esc(j.get("title"))}</b>'
            f'<div class="c">{("★ " if j.get("in_bucket") else "") + _esc(j.get("company"))} · fit {int(j.get("fit_score") or 0)}</div></div>'
            for j in items) or '<div class="muted" style="font-size:12px">—</div>'
        out.append(f'<div class="kcol"><h3>{label}<span>{len(items)}</span></h3>{cards}</div>')
    return f'<div class="kan">{"".join(out)}</div>'


def _tailored_cards(jobs: list[dict], blobmap: dict) -> str:
    done = sorted([j for j in jobs if j.get("status") == "tailored" or j.get("cv_path")],
                  key=lambda j: -(int(j.get("fit_score") or 0)))
    if not done:
        return '<p class="muted">No tailored CVs yet. The pipeline tailors the highest-fit jobs each run.</p>'
    cards = []
    for j in done:
        f = int(j.get("fit_score") or 0)
        star = '<span class="star">★ </span>' if j.get("in_bucket") else ""
        dl = blobmap.get(j.get("dedupe_key"), {})
        links = []
        if dl.get("cv"):
            links.append(f'<a href="{dl["cv"]}" download>⬇ CV (.docx)</a>')
        if dl.get("cover"):
            links.append(f'<a href="{dl["cover"]}" download>⬇ Cover letter (.docx)</a>')
        if j.get("url"):
            links.append(f'<a href="{_esc(j.get("url"))}" target="_blank" rel="noopener">job posting</a>')
        row = " &nbsp;·&nbsp; ".join(links) if links else "CV stored in the database; it will publish on the next run."
        cards.append(
            f'<div class="tcard"><div class="th">{star}{_esc(j.get("title"))} '
            f'<span class="muted">· {_esc(j.get("company"))}</span> <span class="{_fitcls(f)}">fit {f}</span></div>'
            f'<div class="muted tr">{_esc(j.get("fit_reasoning") or "")}</div>'
            f'<div class="tmeta">{row}</div></div>')
    return "".join(cards)


def _runs_table(runs: list[dict]) -> str:
    if not runs:
        return '<p class="muted">No runs logged yet.</p>'
    head = ("<tr><th>Run (UTC)</th><th>Mode</th><th>Disc.</th><th>Targets</th><th>Scored</th>"
            "<th>Tailored</th><th>New</th><th>Note</th></tr>")
    body = "".join(
        f"<tr><td>{_esc(str(r.get('run_at',''))[:16].replace('T',' '))}</td><td>{_esc(r.get('mode'))}</td>"
        f"<td>{_esc(r.get('discovered'))}</td><td>{_esc(r.get('targets'))}</td><td>{_esc(r.get('scored'))}</td>"
        f"<td>{_esc(r.get('tailored'))}</td><td>{_esc(r.get('stored_new'))}</td>"
        f"<td class='muted'>{_esc(r.get('llm_note',''))}</td></tr>" for r in runs[:20])
    return f"<table class='runs'><thead>{head}</thead><tbody>{body}</tbody></table>"


def render(data: dict, note: str, blobmap: dict | None = None) -> str:
    blobmap = blobmap or {}
    jobs = data["jobs"]
    total = len(jobs)
    bucket = sum(1 for j in jobs if j.get("in_bucket"))
    st = data["status"]
    applied = st.get("applied", 0) + st.get("interview", 0) + st.get("offer", 0)
    n_tailored = sum(1 for j in jobs if j.get("status") == "tailored" or j.get("cv_path"))
    n_track = sum(1 for j in jobs if j.get("status") in ("shortlisted", "tailored", "applied", "interview", "offer", "rejected"))
    llm, scoring = data.get("llm", {}), data.get("scoring", {})

    rows = [{
        "title": j.get("title", ""), "company": j.get("company", ""), "source": j.get("source", ""),
        "bucket": bool(j.get("in_bucket")), "fit": int(j.get("fit_score") or 0),
        "status": j.get("status", ""), "posted": str(j.get("posted_date") or "")[:10], "url": j.get("url", ""),
    } for j in jobs]
    blob = json.dumps(rows, default=str).replace("<", "\\u003c")
    statuses = sorted({r["status"] for r in rows if r["status"]})
    sources = sorted({r["source"] for r in rows if r["source"]})
    gen = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    banner = f'<div class="note">{_esc(note)}</div>' if note else ""
    opt = lambda xs: "".join(f'<option value="{_esc(x)}">{_esc(x)}</option>' for x in xs)

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>uk-jobops dashboard</title>
<style>
:root{{--bg:#0e1117;--card:#161b24;--line:#262d3a;--tx:#e6e9ef;--mut:#8b93a7;--ac:#4f8cff;--star:#ffcc4d}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:26px 20px 60px}}
h1{{margin:0 0 2px;font-size:24px}}h2{{font-size:14px;margin:0 0 10px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px}}
.sub{{color:var(--mut);margin:0 0 16px}}
.note{{background:#3a2330;border:1px solid #6b3450;color:#ffd7e3;padding:10px 14px;border-radius:8px;margin:0 0 16px}}
.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:18px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
.kpi .n{{font-size:26px;font-weight:700}}.kpi .l{{color:var(--mut);font-size:12px}}
.tabs{{display:flex;gap:6px;border-bottom:1px solid var(--line);margin-bottom:18px;flex-wrap:wrap}}
.tab{{background:none;border:0;color:var(--mut);padding:9px 14px;font-size:14px;cursor:pointer;border-bottom:2px solid transparent}}
.tab:hover{{color:var(--tx)}}.tab.on{{color:var(--tx);border-bottom-color:var(--ac);font-weight:600}}
.tab .b{{display:inline-block;background:#1f2733;color:var(--mut);border-radius:20px;font-size:11px;padding:0 7px;margin-left:5px}}
.panel{{display:none}}.panel.on{{display:block}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:18px}}
.bar{{display:flex;align-items:center;gap:10px;margin:6px 0}}.bl{{width:120px;color:var(--mut);font-size:12px;text-align:right}}
.bt{{flex:1;background:#0e1117;border-radius:5px;overflow:hidden;height:10px}}.bt i{{display:block;height:100%;background:var(--ac)}}
.bv{{width:34px;text-align:right;color:var(--mut)}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--mut);font-weight:600}}.runs td,.runs th{{white-space:nowrap}}
.muted{{color:var(--mut)}}.star{{color:var(--star)}}a{{color:var(--ac);text-decoration:none}}a:hover{{text-decoration:underline}}
.controls{{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 12px}}
select,input{{background:var(--card);border:1px solid var(--line);color:var(--tx);padding:7px 9px;border-radius:8px;font-size:13px}}
.pill{{display:inline-block;padding:1px 8px;border-radius:20px;background:#1f2733;color:var(--mut);font-size:11px}}
.fit{{font-weight:700}}.fit.hi{{color:#5ad19b}}.fit.mid{{color:#ffce6b}}
.kan{{display:flex;gap:12px;overflow-x:auto;padding-bottom:6px}}
.kcol{{flex:1;min-width:155px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}}
.kcol h3{{margin:0 0 8px;font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px;display:flex;justify-content:space-between}}
.kjob{{border:1px solid var(--line);border-radius:8px;padding:7px 9px;margin-bottom:7px;font-size:12px}}.kjob .c{{color:var(--mut);margin-top:2px}}
.tcard{{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px;background:var(--card)}}
.tcard .th{{font-weight:600;margin-bottom:4px}}.tcard .tr{{font-size:13px;margin-bottom:6px}}.tcard .tmeta{{font-size:12px}}
.foot{{color:var(--mut);font-size:12px;margin-top:24px}}code{{background:#1f2733;padding:1px 6px;border-radius:5px}}
@media(max-width:760px){{.kpis{{grid-template-columns:repeat(2,1fr)}}.cols{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<h1>🧭 uk-jobops</h1>
<p class="sub">Automated UK data-science job discovery, fit-scoring and ATS CV tailoring. Updated {gen}.</p>
{banner}
<div class="kpis">{_kpi('Total jobs', total)}{_kpi('⭐ Bucket-list', bucket)}{_kpi('Shortlisted', st.get('shortlisted',0))}{_kpi('Tailored', st.get('tailored',0))}{_kpi('Applied+', applied)}</div>

<div class="tabs">
<button class="tab on" data-t="overview">Overview</button>
<button class="tab" data-t="jobs">Jobs<span class="b">{total}</span></button>
<button class="tab" data-t="tracker">Tracker<span class="b">{n_track}</span></button>
<button class="tab" data-t="tailored">Tailored<span class="b">{n_tailored}</span></button>
<button class="tab" data-t="pipeline">Pipeline &amp; LLM</button>
</div>

<section class="panel on" id="p-overview">
  <div class="cols">
    <div class="card"><h2>By status</h2>{_bars(st)}</div>
    <div class="card"><h2>By source</h2>{_bars(data['source'])}</div>
  </div>
  <div class="card"><h2>Fit-score distribution</h2>{_fit_bars(jobs)}</div>
  <div class="card"><h2>Top matches</h2>{_top_table(jobs)}</div>
</section>

<section class="panel" id="p-jobs">
  <div class="controls">
    <input id="q" placeholder="Search title / company" style="min-width:220px">
    <select id="fs"><option value="">All statuses</option>{opt(statuses)}</select>
    <select id="fsrc"><option value="">All sources</option>{opt(sources)}</select>
    <select id="sort"><option value="fit">Sort: fit</option><option value="company">Sort: company</option><option value="posted">Sort: posted</option></select>
    <label class="muted"><input type="checkbox" id="fb"> ⭐ only</label>
    <span id="count" class="muted" style="align-self:center"></span>
  </div>
  <div class="card" style="padding:4px 12px"><table id="tbl"><thead><tr>
  <th>Title</th><th>Company</th><th>Source</th><th>⭐</th><th>Fit</th><th>Status</th><th>Posted</th><th></th>
  </tr></thead><tbody id="tb"></tbody></table></div>
</section>

<section class="panel" id="p-tracker">
  <p class="muted" style="margin-top:0">Your application pipeline. Read-only here — edit statuses in the local app.</p>
  {_kanban(jobs)}
</section>

<section class="panel" id="p-tailored">
  <div class="card"><h2>Tailored CVs &amp; cover letters</h2>{_tailored_cards(jobs, blobmap)}</div>
</section>

<section class="panel" id="p-pipeline">
  <div class="card"><h2>LLM configuration (free tiers)</h2>
  <p class="muted" style="margin-top:0">
  Primary model: <b style="color:var(--tx)">{_esc(llm.get('primary','gemini'))} / {_esc(llm.get('gemini_model',''))}</b> &nbsp;·&nbsp;
  Critic: <b style="color:var(--tx)">{_esc(llm.get('critic') or 'off')}</b> &nbsp;·&nbsp;
  Pacing: <b style="color:var(--tx)">{_esc(llm.get('request_delay_seconds',4))}s/call</b> &nbsp;·&nbsp;
  Caps/run: <b style="color:var(--tx)">{_esc(scoring.get('max_score_per_run',40))} scored, {_esc(scoring.get('max_tailor_per_run',6))} tailored</b> &nbsp;·&nbsp;
  Tailor threshold: <b style="color:var(--tx)">fit ≥ {_esc(scoring.get('tailor_threshold',70))}</b></p></div>
  <div class="card"><h2>Recent pipeline runs</h2>{_runs_table(data['runs'])}</div>
</section>

<p class="foot">Read-only snapshot from Supabase, republished every 6 hours by GitHub Actions.
To edit statuses, add jobs, or trigger a run, launch the interactive app locally:
<code>python -m streamlit run app/dashboard.py</code></p>
</div>
<script>
const DATA = {blob};
const tb=document.getElementById('tb'),q=document.getElementById('q'),fs=document.getElementById('fs'),
fsrc=document.getElementById('fsrc'),fb=document.getElementById('fb'),sort=document.getElementById('sort'),cnt=document.getElementById('count');
function esc(s){{return (s||'').replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]))}}
function fitcls(f){{return f>=80?'fit hi':f>=70?'fit mid':'fit'}}
function render(){{
 let r=DATA.slice();
 const t=q.value.toLowerCase(), s=fs.value, sr=fsrc.value, bo=fb.checked, so=sort.value;
 r=r.filter(j=>(!t||(j.title+' '+j.company).toLowerCase().includes(t))&&(!s||j.status===s)&&(!sr||j.source===sr)&&(!bo||j.bucket));
 r.sort((a,b)=> so==='company'?a.company.localeCompare(b.company): so==='posted'?(b.posted||'').localeCompare(a.posted||''): b.fit-a.fit);
 tb.innerHTML=r.map(j=>`<tr><td>${{esc(j.title)}}</td><td>${{esc(j.company)}}</td><td><span class="pill">${{esc(j.source)}}</span></td>
 <td class="star">${{j.bucket?'★':''}}</td><td class="${{fitcls(j.fit)}}">${{j.fit||''}}</td><td>${{esc(j.status)}}</td>
 <td class="muted">${{esc(j.posted)}}</td><td>${{j.url?`<a href="${{esc(j.url)}}" target="_blank" rel="noopener">open</a>`:''}}</td></tr>`).join('');
 cnt.textContent=r.length+' / '+DATA.length+' jobs';
}}
[q,fs,fsrc,fb,sort].forEach(el=>el.addEventListener('input',render));render();
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>{{
 document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
 document.querySelectorAll('.panel').forEach(x=>x.classList.remove('on'));
 b.classList.add('on');document.getElementById('p-'+b.dataset.t).classList.add('on');
}}));
</script></body></html>"""


def main() -> None:
    OUT.mkdir(exist_ok=True)
    note, blobmap = "", {}
    try:
        data = _gather()
        blobmap = _write_cvs(data.get("blobs", []), OUT / "cvs")
    except Exception as exc:  # ConfigError / connection issue -> still publish a page
        data = {"jobs": [], "runs": [], "status": {}, "source": {}, "blobs": [], "llm": {}, "scoring": {}}
        note = f"Database not reachable yet ({type(exc).__name__}). Fix SUPABASE_DB_URL and the next run will populate this."
    (OUT / "index.html").write_text(render(data, note, blobmap), encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Wrote {OUT / 'index.html'} with {len(data['jobs'])} jobs, {len(blobmap)} downloadable CVs; note={note or 'none'}")


if __name__ == "__main__":
    main()
