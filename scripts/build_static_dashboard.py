"""Render a self-contained static dashboard to site/index.html from the Supabase data.
The Actions workflow publishes site/ to GitHub Pages after every run, so it is
continuously viewable (read-only). Editing the tracker is done in the Streamlit app."""
from __future__ import annotations

import datetime as dt
import html
import json
import pathlib
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
        "llm": cfg.settings.get("llm", {}),
        "scoring": cfg.settings.get("scoring", {}),
    }
    store.close()
    return data


def _esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _kpi(label: str, value) -> str:
    return f'<div class="kpi"><div class="n">{_esc(value)}</div><div class="l">{_esc(label)}</div></div>'


def _bars(counts: dict) -> str:
    if not counts:
        return '<p class="muted">No data yet.</p>'
    top = max(counts.values()) or 1
    rows = []
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        pct = int(v / top * 100)
        rows.append(f'<div class="bar"><span class="bl">{_esc(k)}</span>'
                    f'<span class="bt"><i style="width:{pct}%"></i></span><span class="bv">{v}</span></div>')
    return "".join(rows)


def _runs_table(runs: list[dict]) -> str:
    if not runs:
        return '<p class="muted">No runs logged yet.</p>'
    head = "<tr><th>Run (UTC)</th><th>Mode</th><th>Disc.</th><th>Targets</th><th>Scored</th><th>Tailored</th><th>New</th><th>Note</th></tr>"
    body = []
    for r in runs[:20]:
        body.append(
            f"<tr><td>{_esc(str(r.get('run_at',''))[:16].replace('T',' '))}</td><td>{_esc(r.get('mode'))}</td>"
            f"<td>{_esc(r.get('discovered'))}</td><td>{_esc(r.get('targets'))}</td><td>{_esc(r.get('scored'))}</td>"
            f"<td>{_esc(r.get('tailored'))}</td><td>{_esc(r.get('stored_new'))}</td>"
            f"<td class='muted'>{_esc(r.get('llm_note',''))}</td></tr>")
    return f"<table class='runs'>{head}{''.join(body)}</table>"


def render(data: dict, note: str) -> str:
    jobs = data["jobs"]
    total = len(jobs)
    bucket = sum(1 for j in jobs if j.get("in_bucket"))
    st = data["status"]
    applied = st.get("applied", 0) + st.get("interview", 0) + st.get("offer", 0)
    llm, scoring = data.get("llm", {}), data.get("scoring", {})

    # compact rows for the client-side table
    rows = [{
        "title": j.get("title", ""), "company": j.get("company", ""), "source": j.get("source", ""),
        "bucket": bool(j.get("in_bucket")), "fit": int(j.get("fit_score") or 0),
        "status": j.get("status", ""), "posted": str(j.get("posted_date") or "")[:10],
        "url": j.get("url", ""),
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
.wrap{{max-width:1180px;margin:0 auto;padding:28px 20px 60px}}
h1{{margin:0 0 2px;font-size:24px}}h2{{font-size:15px;margin:26px 0 10px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px}}
.sub{{color:var(--mut);margin:0 0 18px}}
.note{{background:#3a2330;border:1px solid #6b3450;color:#ffd7e3;padding:10px 14px;border-radius:8px;margin:0 0 18px}}
.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
.kpi .n{{font-size:26px;font-weight:700}}.kpi .l{{color:var(--mut);font-size:12px}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}}
.bar{{display:flex;align-items:center;gap:10px;margin:6px 0}}.bl{{width:120px;color:var(--mut);font-size:12px;text-align:right}}
.bt{{flex:1;background:#0e1117;border-radius:5px;overflow:hidden;height:10px}}.bt i{{display:block;height:100%;background:var(--ac)}}
.bv{{width:34px;text-align:right;color:var(--mut)}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--mut);font-weight:600;cursor:default}}.runs td,.runs th{{white-space:nowrap}}
.muted{{color:var(--mut)}}.star{{color:var(--star)}}a{{color:var(--ac);text-decoration:none}}a:hover{{text-decoration:underline}}
.controls{{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0 12px}}
select,input{{background:var(--card);border:1px solid var(--line);color:var(--tx);padding:7px 9px;border-radius:8px;font-size:13px}}
.pill{{display:inline-block;padding:1px 8px;border-radius:20px;background:#1f2733;color:var(--mut);font-size:11px}}
.fit{{font-weight:700}}.fit.hi{{color:#5ad19b}}.fit.mid{{color:#ffce6b}}
.foot{{color:var(--mut);font-size:12px;margin-top:26px}}
@media(max-width:760px){{.kpis{{grid-template-columns:repeat(2,1fr)}}.cols{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<h1>🧭 uk-jobops</h1>
<p class="sub">Automated UK data-science job discovery, fit-scoring and ATS CV tailoring. Updated {gen}.</p>
{banner}
<div class="kpis">{_kpi('Total jobs', total)}{_kpi('⭐ Bucket-list', bucket)}{_kpi('Shortlisted', st.get('shortlisted',0))}{_kpi('Tailored', st.get('tailored',0))}{_kpi('Applied+', applied)}</div>

<div class="cols" style="margin-top:18px">
<div class="card"><h2 style="margin-top:0">By status</h2>{_bars(st)}</div>
<div class="card"><h2 style="margin-top:0">By source</h2>{_bars(data['source'])}</div>
</div>

<h2>Pipeline & LLM</h2>
<div class="card muted">
Primary model: <b style="color:var(--tx)">{_esc(llm.get('primary','gemini'))} / {_esc(llm.get('gemini_model',''))}</b> &nbsp;·&nbsp;
Critic: <b style="color:var(--tx)">{_esc(llm.get('critic') or 'off')}</b> &nbsp;·&nbsp;
Pacing: <b style="color:var(--tx)">{_esc(llm.get('request_delay_seconds',4))}s/call</b> &nbsp;·&nbsp;
Caps/run: <b style="color:var(--tx)">{_esc(scoring.get('max_score_per_run',40))} scored, {_esc(scoring.get('max_tailor_per_run',6))} tailored</b> &nbsp;·&nbsp;
Tailor threshold: <b style="color:var(--tx)">fit ≥ {_esc(scoring.get('tailor_threshold',70))}</b>
<div style="margin-top:12px">{_runs_table(data['runs'])}</div>
</div>

<h2>Jobs</h2>
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

<p class="foot">Read-only snapshot from Supabase, republished every 6 hours by GitHub Actions.
To edit statuses, add jobs, or trigger a run, launch the Streamlit app locally:
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
</script></body></html>"""


def main() -> None:
    OUT.mkdir(exist_ok=True)
    note = ""
    try:
        data = _gather()
    except Exception as exc:  # ConfigError / connection issue -> still publish a page
        data = {"jobs": [], "runs": [], "status": {}, "source": {}, "llm": {}, "scoring": {}}
        note = f"Database not reachable yet ({type(exc).__name__}). Fix SUPABASE_DB_URL and the next run will populate this."
    (OUT / "index.html").write_text(render(data, note), encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Wrote {OUT / 'index.html'} with {len(data['jobs'])} jobs; note={note or 'none'}")


if __name__ == "__main__":
    main()
