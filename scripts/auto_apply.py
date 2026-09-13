"""Phase-1 AI apply agent (RUN ON YOUR MAC, not in GitHub Actions).

For each direct-employer ATS role in the Apply Queue (score >= threshold; never Indeed/LinkedIn):
opens the real application FORM, reads EVERY field (incl. dropdowns / radios / custom questions),
answers them AS you (LLM, using your profile + your standard answers in settings apply.answers),
attaches the role-matched CV, and — with --submit / apply.auto_submit — clicks Submit. Then it
screenshots the result, sends it to Telegram, logs it (dashboard Apply Activity), and marks applied.

Setup once:  pip install playwright && playwright install chromium
Run:
    python scripts/auto_apply.py                 # fill everything, PAUSE before submit (review)
    python scripts/auto_apply.py --submit        # fill everything AND submit automatically
    python scripts/auto_apply.py --submit --min-score 90 --limit 10
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os as _os
import pathlib
import sys
import time
from urllib.parse import urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

_VERSION = "v6.2 (cloud runs are watchable: step screenshots + video)"
# Groups this run's step screenshots together (GitHub run id in the cloud, timestamp locally).
RUN_ID = _os.environ.get("GITHUB_RUN_ID") or dt.datetime.now().strftime("%Y%m%d-%H%M%S")

from uk_jobops import notify  # noqa: E402
from uk_jobops import applicant  # noqa: E402
from uk_jobops.config import load_config  # noqa: E402
from uk_jobops.cv_match import CV_FILES, CV_LABEL, match_cv_key  # noqa: E402

# read every fillable field on the page, tag it with data-apply-id, return its label + options
JS_EXTRACT = r'''
() => {
  const out = [], seenRadio = {};
  let i = 0;
  const labelOf = (el) => {
    let t = el.getAttribute('aria-label') || '';
    if (!t && el.id) { const l = document.querySelector('label[for="' + el.id + '"]'); if (l) t = l.innerText; }
    if (!t) {
      let p = el.closest('li,.application-question,.form-group,fieldset,section,div'); let hops = 0;
      while (p && !t && hops < 5) {
        const lab = p.querySelector('label,.application-label,legend,h2,h3,h4,p');
        if (lab && lab.innerText.trim()) t = lab.innerText.trim();
        p = p.parentElement; hops++;
      }
    }
    if (!t) t = el.getAttribute('placeholder') || el.getAttribute('name') || '';
    return (t || '').replace(/\s+/g, ' ').trim().slice(0, 180);
  };
  // for radio/checkbox GROUPS: return the QUESTION, not the option's own label (which wraps an input)
  const groupLabelOf = (el) => {
    const own = el.closest('label');
    let p = el.closest('.application-question, fieldset, li, .form-group, section, div'); let hops = 0;
    while (p && hops < 7) {
      const cand = p.querySelector('.application-label, legend, h2, h3, h4');
      if (cand && cand !== own && cand.innerText.trim()) return cand.innerText.replace(/\s+/g, ' ').trim().slice(0, 250);
      const labs = [...p.querySelectorAll('label')].filter(l => l !== own && l.innerText.trim()
                     && !l.querySelector('input[type=radio],input[type=checkbox]'));
      if (labs.length) return labs[0].innerText.replace(/\s+/g, ' ').trim().slice(0, 250);
      p = p.parentElement; hops++;
    }
    return (el.getAttribute('aria-label') || el.name || '').slice(0, 250);
  };
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'file', 'search'].includes(type)) continue;
    if (el.offsetParent === null && type !== 'radio' && type !== 'checkbox') continue;  // skip invisible
    el.setAttribute('data-apply-id', i);
    if (type === 'radio') {
      const name = el.name || ('r' + i);
      let ol = ''; const par = el.closest('label'); if (par) ol = par.innerText.trim();
      if (!ol && el.id) { const l = document.querySelector('label[for="' + el.id + '"]'); if (l) ol = l.innerText.trim(); }
      if (!seenRadio[name]) seenRadio[name] = { i, label: groupLabelOf(el), options: [] };
      seenRadio[name].options.push({ apply_id: i, text: (ol || el.value || '').replace(/\s+/g, ' ').trim() });
      i++; continue;
    }
    if (type === 'checkbox') { out.push({ apply_id: i, kind: 'checkbox', label: labelOf(el) }); i++; continue; }
    if (el.tagName.toLowerCase() === 'select') {
      const opts = [...el.options].map(o => o.text.replace(/\s+/g, ' ').trim()).filter(Boolean);
      out.push({ apply_id: i, kind: 'select', label: labelOf(el), options: opts }); i++; continue;
    }
    out.push({ apply_id: i, kind: 'text', label: labelOf(el) }); i++;
  }
  for (const n in seenRadio) { const r = seenRadio[n]; out.push({ apply_id: r.i, kind: 'radio', label: r.label, options: r.options }); }
  return out;
}
'''

_SYSTEM = (
    "You are completing a job application form AS this candidate — think and answer exactly as they "
    "would, using ONLY the CANDIDATE BRIEF provided (master profile > the attached CV > extra facts). "
    "Never invent qualifications, tools, employers, dates or figures not in the brief; if the brief "
    "lacks something, give the most honest, closest real answer and never overclaim. For a dropdown "
    "or radio, return the EXACT option text that best fits from the provided options. For consent / "
    "GDPR / 'I agree' / privacy checkboxes return 'yes'. For an optional 'if other, specify' that "
    "doesn't apply, return ''.\n"
    "For OPEN tech/business questions (e.g. 'overview of your SQL experience', 'why this company', "
    "'describe a project'): write a strong, first-person, human answer that would score 5/5 with a "
    "hiring manager AND pass ATS keyword screening. To do that: (1) mirror the exact terminology and "
    "tech stack from the JOB DESCRIPTION where the candidate genuinely has that experience (e.g. SQL, "
    "Python, pandas/NumPy, Git, dbt, Tableau/Looker/Power BI, credit risk, fraud, vulnerable "
    "customers, ML models, KPIs); (2) back claims with the candidate's REAL evidence and metrics from "
    "the brief (e.g. 1M+ claims, 200K+ customers, MMM +8%, RFM +14%/-12 days); (3) map the "
    "candidate's domain to THIS domain (energy/credit-risk/collections/fraud) explicitly; (4) be "
    "honest about gaps — if the JD wants dbt and the candidate hasn't used it, say so and pivot to the "
    "closest real equivalent (ETL, Azure Data Factory, Databricks). Keep open answers 3-6 sentences, "
    "specific not generic, confident not boastful, natural human tone (no buzzword salad, no "
    "em-dashes, UK spelling). Match the candidate's standard answers where a question matches one. "
    "Return JSON only."
)


def _facts(cfg, cv_path: str = "", cv_label: str = "", job: dict | None = None) -> str:
    """The full 'answer like me' brief: master profile + the exact attached CV's text + extra facts."""
    return "CANDIDATE BRIEF:\n" + applicant.build_brain(cfg, cv_path=cv_path, cv_label=cv_label, job=job)


def agent_answer(llm, facts: str, fields: list[dict]) -> dict:
    """One LLM call -> {apply_id: value}. value is the option text (select/radio), 'yes' (checkbox), or text."""
    slim = [{"apply_id": f["apply_id"], "kind": f["kind"], "question": f.get("label", ""),
             "options": [o["text"] for o in f.get("options", [])] if f.get("options") else None}
            for f in fields]
    user = (facts + "\n\nFORM FIELDS (answer each):\n" + json.dumps(slim, ensure_ascii=False)
            + '\n\nReturn JSON: {"answers": {"<apply_id>": "<value>"}} — value = exact option text for '
            "select/radio, 'yes'/'no' for checkbox, the text for text fields, '' to skip.")
    try:
        data = llm.complete_json(_SYSTEM, user, quality=True)
    except Exception as exc:
        print(f"    ! agent LLM error: {str(exc)[:100]}")
        return {}
    return {str(k): v for k, v in (data.get("answers") or data).items()}


def _click_choice(page, aid) -> bool:
    """Reliably select a radio/checkbox on ATSes (Lever/Greenhouse) that HIDE the real input and show
    a styled label: click the label (fires native selection + the ATS's own handler), then set checked
    and dispatch change as a belt-and-braces. Returns True if the input ended up checked."""
    try:
        return bool(page.evaluate(
            """(id) => {
              const el = document.querySelector('[data-apply-id="' + id + '"]');
              if (!el) return false;
              let lab = el.closest('label');
              if (!lab && el.id) lab = document.querySelector('label[for="' + el.id + '"]');
              if (!lab) { const p = el.parentElement; if (p) lab = p.querySelector('label') || p; }
              try { (lab || el).click(); } catch (e) {}
              try { el.checked = true;
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true})); } catch (e) {}
              return !!el.checked;
            }""", aid))
    except Exception:
        return False


def _fill_field(page, field: dict, value) -> bool:
    if value in (None, ""):
        return False
    i = field["apply_id"]
    sel = f'[data-apply-id="{i}"]'
    kind = field["kind"]
    lab = (field.get("label") or "").lower()
    try:
        if kind == "text":
            if "location" in lab:                        # Lever/Greenhouse location autocomplete
                return _fill_location(page, sel, str(value))
            page.fill(sel, str(value), timeout=2500); return True
        if kind == "select":
            vl = str(value).strip().lower()
            try:
                page.select_option(sel, label=str(value), timeout=2500); return True
            except Exception:
                for opt in field.get("options", []):          # exact (case-insensitive) first
                    if opt.strip().lower() == vl:
                        page.select_option(sel, label=opt, timeout=2500); return True
                for opt in field.get("options", []):          # then substring
                    if vl in opt.lower() or opt.lower() in vl:
                        page.select_option(sel, label=opt, timeout=2500); return True
        if kind == "checkbox":
            if str(value).strip().lower() in ("yes", "true", "1", "y", "agree"):
                if not _click_choice(page, i):               # Lever hides the real box; click its label
                    page.check(sel, force=True, timeout=2500)
                return True
        if kind == "radio":
            aid = applicant.pick_radio_option(field.get("options", []), value)  # exact-first ('Man'≠'Woman')
            if aid is not None:
                if not _click_choice(page, aid):             # click the visible label so Lever's UI updates
                    page.check(f'[data-apply-id="{aid}"]', force=True, timeout=2500)
                return True
    except Exception:
        return False
    return False


def _fill_location(page, sel, value: str) -> bool:
    """Location fields (e.g. Lever '#location-input') are geocode autocompletes. A plain fill() is
    WIPED on blur because no suggestion was selected (confirmed on Lever). The only thing that sticks
    is actually SELECTING a geocoded suggestion. So: type with real keystrokes (fires the geocode),
    wait for the dropdown, then ArrowDown+Enter to select the first result (also try clicking it).
    Finally verify the value stuck; if the geocode returned nothing, leave the typed text."""
    _COUNT_JS = ("() => { const c = document.querySelector('.dropdown-results'); if (!c) return 0; "
                 "return [...c.querySelectorAll('*')].filter(n => n.innerText && n.innerText.trim()).length; }")
    # Lever commits a pick on the item's mousedown/click — fire the full mouse sequence, not just click
    _CLICK_JS = ("() => { const c = document.querySelector('.dropdown-results'); if (!c) return false; "
                 "const it = [...c.querySelectorAll('*')].find(n => n.children.length === 0 && n.innerText && "
                 "n.innerText.trim()) || c.firstElementChild; if (!it) return false; "
                 "for (const t of ['mousedown','mouseup','click']) "
                 "it.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true})); return true; }")
    # Lever commits the pick to a hidden #selected-location — the definitive 'it stuck' signal
    _HAS_HIDDEN_JS = "() => !!document.querySelector('#selected-location, [name=selectedLocation]')"
    _COMMITTED_JS = ("() => { const h = document.querySelector('#selected-location, [name=selectedLocation]'); "
                     "return !!(h && h.value && h.value.trim()); }")

    def _committed(el, lever):
        try:
            v = (el.input_value() or "").strip()
            if lever:
                if bool(page.evaluate(_COMMITTED_JS)):        # hidden field set = definitely committed
                    return True
                # a picked suggestion REPLACES the typed text (e.g. adds ', England, GBR')
                return bool(v) and v.lower() != value.strip().lower()
            return bool(v)                                     # plain field: any text counts
        except Exception:
            return False

    try:
        el = page.locator(sel).first
        try:
            el.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        try:
            lever = bool(page.evaluate(_HAS_HIDDEN_JS))
        except Exception:
            lever = False
        for _attempt in range(4):
            el.click(timeout=2500)
            try:
                el.fill("")
            except Exception:
                pass
            try:
                el.press_sequentially(value, delay=80)    # real per-key input → triggers the geocode
            except Exception:
                el.type(value, delay=80)                   # older Playwright
            # wait (up to ~8s) for the geocode suggestion dropdown to actually have items
            has_dd = False
            for _ in range(20):
                page.wait_for_timeout(400)
                try:
                    if page.evaluate(_COUNT_JS):
                        has_dd = True
                        break
                except Exception:
                    pass
            if has_dd:
                page.wait_for_timeout(300)                  # settle so the list is interactable
                # SELECT the first suggestion — keyboard (Lever's own handler) then full mouse sequence
                try:
                    el.press("ArrowDown"); el.press("Enter")
                except Exception:
                    pass
                page.wait_for_timeout(800)
                if _committed(el, lever):
                    return True
                try:
                    page.evaluate(_CLICK_JS)
                except Exception:
                    pass
                page.wait_for_timeout(800)
                if _committed(el, lever):
                    return True
                # dropdown was there but didn't commit — retry the whole type+select
            elif not lever:
                # NO dropdown and this is a plain text location field → the typed text persists
                return True
            # Lever with no dropdown this attempt → geocode may respond on the next try; loop
        # FALLBACK ('if no dropdown, fill the address'): put the text in so the box isn't empty.
        # On Lever this is best-effort (it may still require a manual pick); on plain fields it sticks.
        try:
            el.click(timeout=1500)
            el.fill(value)
        except Exception:
            pass
        return _committed(el, lever)
    except Exception:
        return False


def _has_captcha(page) -> bool:
    """Detect a CAPTCHA / bot-check on the page (reCAPTCHA, hCaptcha, Cloudflare, Turnstile)."""
    try:
        return bool(page.evaluate(
            "() => { const q = document.querySelector.bind(document);"
            " return !!(q('iframe[src*=\"recaptcha\"]') || q('iframe[src*=\"hcaptcha\"]')"
            " || q('iframe[title*=\"captcha\" i]') || q('.g-recaptcha') || q('.h-captcha')"
            " || q('[class*=\"cf-turnstile\"]') || q('[data-sitekey]')); }"))
    except Exception:
        return False


def _upload_cv(page, cv_path: str) -> bool:
    if not cv_path:
        return False
    ok = False
    for fi in page.query_selector_all("input[type=file]"):
        try:
            fi.set_input_files(cv_path); time.sleep(0.6); ok = True
        except Exception:
            continue
    return ok


def open_form(page, url: str) -> None:
    host = urlparse(url).netloc.lower()
    target = url.rstrip("/") + "/apply" if ("lever.co" in host and not url.rstrip("/").endswith("/apply")) else url
    page.goto(target, timeout=45000, wait_until="domcontentloaded")
    time.sleep(1.5)
    if "lever.co" not in host:
        for s in ["text=Apply for this job", "text=Apply now", "text=Apply Now",
                  "button:has-text('Apply')", "a:has-text('Apply')", "text=I'm interested"]:
            try:
                b = page.locator(s).first
                if b.count() and b.is_visible():
                    b.click(timeout=2000); time.sleep(1.5); break
            except Exception:
                continue


def _page_job_meta(page) -> tuple:
    """Extract (title, description) from an open job page — used to pick the right CV for a PASTED URL."""
    try:
        data = page.evaluate(
            "() => { const t = (document.querySelector('h1,h2') && document.querySelector('h1,h2').innerText)"
            " || document.title || ''; const m = document.querySelector('main,article,.content,.posting,"
            "[class*=description]') || document.body; const d = (m.innerText || '').replace(/\\s+/g,' ').trim();"
            " return {title: t.replace(/\\s+/g,' ').trim().slice(0,140), desc: d.slice(0,2500)}; }")
        return (data.get("title", ""), data.get("desc", ""))
    except Exception:
        return ("", "")


def _resolve_cv(cv_dir, title: str, desc: str, cat, forced: str = ""):
    """Pick the best CV for a job → (key, cv_path, label). Never trusts a stale value; matches the JD."""
    try:
        key = forced or match_cv_key(title or "", desc or "", cat) or "ds-azure"
    except Exception:
        key = forced or "ds-azure"
    cv_file = CV_FILES.get(key, "")
    cv_path = str(cv_dir / cv_file) if cv_file and (cv_dir / cv_file).exists() else ""
    return key, cv_path, CV_LABEL.get(key, key)


def _company_from_url(url: str) -> str:
    """Best-effort employer name from an ATS URL (jobs.lever.co/<company>/…, boards.greenhouse.io/<company>/…)."""
    try:
        pr = urlparse(url)
        segs = [s for s in pr.path.split("/") if s]
        if segs and segs[0] not in ("jobs", "careers", "job"):
            return segs[0].replace("-", " ").replace("_", " ").title()
        return pr.netloc.replace("www.", "")
    except Exception:
        return url


def submit_form(page) -> bool:
    for s in ["button:has-text('Submit application')", "button:has-text('Submit Application')",
              "button:has-text('Submit')", "button[type=submit]", "text=Submit Application"]:
        try:
            b = page.locator(s).first
            if b.count() and b.is_visible():
                b.click(timeout=4000); return True
        except Exception:
            continue
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=int, default=None)
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--submit", action="store_true", help="click Submit automatically (else pause for review)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--retest", action="store_true",
                    help="re-surface already-applied roles too (local testing only)")
    ap.add_argument("--url", default=None,
                    help="TEST one specific application URL directly, bypassing the DB queue")
    ap.add_argument("--title", default="", help="role title for --url (helps pick the right CV)")
    ap.add_argument("--cv", default="", help="force a CV key for --url (e.g. ai-engineer, ds-azure)")
    ap.add_argument("--from-queue", dest="from_queue", action="store_true",
                    help="apply to the URLs you queued from the Auto-Apply panel (apply_requests)")
    ap.add_argument("--local-browser", dest="local_browser", action="store_true",
                    help="force a local Chrome even if STEEL_API_KEY is set (no live viewer)")
    ap.add_argument("--ci", action="store_true",
                    help="cloud/unattended mode: never wait for keyboard input; CAPTCHA-blocked forms are "
                         "flagged 'needs_manual' instead of pausing")
    args = ap.parse_args()

    # Unattended when --ci, when running in CI (GitHub Actions sets CI=true), or with no terminal.
    interactive = not (args.ci or _os.environ.get("CI") == "true" or not sys.stdin.isatty())

    def pause(msg: str) -> None:
        """Wait for the user ONLY when a human is actually there; otherwise just log and continue."""
        if interactive:
            input(msg)
        else:
            print(f"      (unattended: {msg.strip()[:90]})")

    cfg = load_config()
    acfg = cfg.settings.get("apply", {})
    sec = cfg.secrets
    if not sec.supabase_db_url:
        print("No SUPABASE_DB_URL set."); return
    from uk_jobops.db import Store
    from uk_jobops.llm.client import LLM
    from uk_jobops.tracker import Tracker
    store = Store(sec.supabase_db_url); store.init_schema()
    thr = args.min_score if args.min_score is not None else int(acfg.get("threshold", 95))
    auto_submit = args.submit or bool(acfg.get("auto_submit", False))

    if args.url:
        # DIRECT TEST: apply to one specific form, bypassing the DB queue entirely.
        key = args.cv or match_cv_key(args.title, "", "")
        queue = [{"dedupe_key": "", "title": args.title or "Application",
                  "company": _company_from_url(args.url), "url": args.url, "fit_score": 0,
                  "matched_cv": key, "country": "United Kingdom", "location": "", "locations": "",
                  "category": "", "sector": "", "status": "test",
                  "_auto_submit": auto_submit, "_needs_meta": not args.title}]
        print(f"TEST mode: applying to 1 URL with CV '{CV_LABEL.get(key, key)}'.\n")
    elif args.from_queue:
        # URLs the user queued from the Streamlit Auto-Apply control panel (apply_requests table).
        reqs = store.apply_requests("queued", limit=args.limit)
        if not reqs:
            print("Auto-Apply queue is empty. Add URLs from the Streamlit 'Auto-Apply' tab (phone or laptop), "
                  "then re-run.  python scripts/auto_apply.py --from-queue")
            store.close(); return
        queue = [{"dedupe_key": "", "title": r.get("title", "") or "",
                  "company": r.get("company", "") or _company_from_url(r["url"]),
                  "url": r["url"], "fit_score": 0, "matched_cv": "", "country": "United Kingdom",
                  "location": "", "locations": "", "category": "", "sector": "", "status": "queued",
                  "_req_id": r["id"], "_auto_submit": bool(r["auto_submit"]),
                  "_needs_meta": (r.get("source") == "pasted" or not r.get("title"))}
                 for r in reqs]
    else:
        queue = store.apply_queue(min_score=thr, limit=args.limit, include_applied=args.retest)
    if not queue:
        s, top = store.apply_stats(thr=thr)
        print(f"Apply queue empty at fit >= {thr}.")
        print(f"  Scored: total {s['total']} | >=90 {s['ge90']} | >=85 {s['ge85']} | >=80 {s['ge80']}")
        print(f"  Direct-employer ATS (fillable): total {s['ats_total']} | >=90 {s['ats90']} | >=85 {s['ats85']}")
        print(f"  WHY the queue is empty at >= {thr}:")
        print(f"    • {s['queue']} truly eligible (direct-ATS, target, not yet applied) ← what would run")
        print(f"    • {s['ats_thr']} are on a direct ATS at >= {thr}, of which "
              f"{s['applied_thr']} already applied, {s['nontarget_thr']} not a target role")
        print(f"    • {s['agg_thr']} high-fit roles (>= {thr}) are stuck on LinkedIn/Indeed URLs — "
              f"NOT auto-fillable (apply to those manually)")
        print(f"  Top direct-ATS roles (score · status · title · company):")
        for r in (top or [])[:10]:
            print(f"    {int(r['fit_score'] or 0):>3}  {str(r.get('status') or '?')[:10]:<10}  "
                  f"{(r['title'] or '')[:38]:<38}  {(r['company'] or '')[:20]}")
        print("\n  To WATCH the agent fill a form now, test one URL directly, e.g.:")
        print("    python scripts/auto_apply.py --url \"<paste a Lever/Greenhouse job URL>\" --title \"Data Scientist\"")
        print("  Or re-run an already-applied role for testing:  python scripts/auto_apply.py --retest --min-score 80")
        store.close(); return

    cv_dir = pathlib.Path(cfg.path(acfg.get("cv_dir", "data/cvs")))
    shot_dir = pathlib.Path(cfg.path("output/apply_shots")); shot_dir.mkdir(parents=True, exist_ok=True)
    llm = LLM(cfg)
    trk = Tracker(sec.supabase_db_url); trk.init_schema()
    tg = (sec.telegram_bot_token, sec.telegram_chat_id)
    print(f"auto_apply {_VERSION}  |  {len(queue)} role(s) to apply to (>= {thr}). auto_submit={auto_submit}")
    print(f"RUN_ID={RUN_ID}  (watch it live in the app → Auto-Apply → Live run)\n")
    store.log_apply_event(run_id=RUN_ID, kind="run_start", field="queued",
                          answer=f"{len(queue)} application(s)", origin="auto")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed:  pip install playwright && playwright install chromium")
        store.close(); return

    with sync_playwright() as p:
        # ---------------------------------------------------------------- browser: Steel or local
        # With a STEEL_API_KEY we drive a CLOUD browser and get a LIVE VIEWER URL — open it on your
        # phone or Mac and watch the form being filled in real time. Otherwise launch Chrome locally.
        steel_key = _os.environ.get("STEEL_API_KEY", "")
        use_steel = bool(steel_key) and not args.local_browser
        steel_client = steel_session = None
        live_url = ""
        if use_steel:
            try:
                from steel import Steel
                steel_client = Steel(steel_api_key=steel_key)
                # NOTE: solve_captcha intentionally NOT enabled — CAPTCHAs are left for you.
                steel_session = steel_client.sessions.create()
                # The EMBEDDABLE, no-login viewer is the session's debug URL (app.steel.dev/sessions/…
                # is the dashboard and requires a Steel login, so it can't be embedded).
                live_url = getattr(steel_session, "debug_url", "") or ""
                if not live_url:
                    try:
                        _dbg = steel_client.sessions.debug(steel_session.id)
                        live_url = (getattr(_dbg, "debugger_fullscreen_url", "")
                                    or getattr(_dbg, "debuggerFullscreenUrl", "")
                                    or getattr(_dbg, "debugger_url", "") or "")
                    except Exception:
                        live_url = ""
                if live_url:                       # interactive = you can take over mid-run
                    sep = "&" if "?" in live_url else "?"
                    live_url = f"{live_url}{sep}interactive=true&showControls=true"
                dash_url = f"https://app.steel.dev/sessions/{steel_session.id}"
                print("\n" + "=" * 68)
                print(f"📺  WATCH IT LIVE:  {live_url or dash_url}")
                print(f"    (dashboard/replay: {dash_url})")
                print("=" * 68 + "\n")
                live_url = live_url or dash_url
                store.log_apply_event(run_id=RUN_ID, kind="live_view", field="Watch live",
                                      answer=live_url, origin="auto")
                browser = p.chromium.connect_over_cdp(f"{steel_session.websocket_url}&apiKey={steel_key}")
                ctx = browser.contexts[0]          # Steel hands back a ready context
            except Exception as exc:
                print(f"! Steel unavailable ({str(exc)[:120]}) — falling back to a local browser.")
                use_steel = False
                steel_client = steel_session = None
        if not use_steel:
            browser = p.chromium.launch(headless=args.headless)
            _vid_dir = pathlib.Path(cfg.path("output/apply_videos"))
            _ctx_kw = {"accept_downloads": True, "viewport": {"width": 1440, "height": 1000}}
            if not interactive:                    # record video so an unattended run is watchable back
                _vid_dir.mkdir(parents=True, exist_ok=True)
                _ctx_kw["record_video_dir"] = str(_vid_dir)
                _ctx_kw["record_video_size"] = {"width": 1440, "height": 1000}
                print(f"Recording video of this run -> {_vid_dir}")
            ctx = browser.new_context(**_ctx_kw)
        for idx, job in enumerate(queue, 1):
            # recompute the CV from the live JD every time (never trust a stale/empty stored value —
            # e.g. a 'Data Analyst' role must get the Data Analyst CV, not the default ds-azure)
            key, cv_path, label = _resolve_cv(cv_dir, job.get("title", ""), job.get("description", "") or "",
                                              job.get("category"), job.get("matched_cv") or "")
            do_submit = bool(job.get("_auto_submit", auto_submit)) or args.submit
            req_id = job.get("_req_id")
            print(f"[{idx}/{len(queue)}] {job.get('title') or '(pasted URL)'} — {job.get('company')} "
                  f"(fit {job.get('fit_score')}, CV {label}, submit={do_submit})")
            if req_id:
                try:
                    store.set_apply_request_status(req_id, "processing")
                except Exception:
                    pass
            page = ctx.new_page()
            submitted = False
            filled = 0
            blocked = False
            _step = {"n": 0}

            def snap(label: str, note: str = "") -> None:
                """Screenshot this stage into the DB so a headless CLOUD run is watchable in the app."""
                _step["n"] += 1
                try:
                    img = page.screenshot(full_page=True)
                except Exception:
                    return
                try:
                    store.log_apply_step(url=job.get("url", ""), company=job.get("company", ""),
                                         role_title=job.get("title", ""), run_id=RUN_ID,
                                         step_no=_step["n"], label=label, note=note, shot=img)
                except Exception:
                    pass

            store.log_apply_event(run_id=RUN_ID, url=job.get("url", ""), company=job.get("company", ""),
                                  role_title=job.get("title", ""), kind="job_start",
                                  field="CV", answer=label, origin="auto")
            try:
                open_form(page, job.get("url"))
                snap("1 · Application form opened")
                # for a PASTED URL (no title), read the page to auto-detect the role and pick the right CV
                if job.get("_needs_meta"):
                    mt, md = _page_job_meta(page)
                    job["title"] = job.get("title") or mt
                    job["description"] = md
                    key, cv_path, label = _resolve_cv(cv_dir, job.get("title") or mt, md, None, "")
                    print(f"      detected role: {(job['title'] or '?')[:46]}  →  CV {label}")
                uploaded = _upload_cv(page, cv_path)
                fields = page.evaluate(JS_EXTRACT)
                if not fields:
                    print("    ! no form fields found on the page — check the URL opens the real APPLY form.")
                # 1) DETERMINISTIC fill from the profile (name/email/visa/salary/diversity/... — no LLM,
                #    so the form fills even if the model is unavailable)
                det = applicant.deterministic_fill(cfg, fields)
                # 2) LLM only for the fields the matcher left blank (open questions like 'why this role')
                remaining = [f for f in fields if str(f["apply_id"]) not in det]
                llm_ans = {}
                if remaining and any(f["kind"] in ("text", "textarea") or not f.get("options") for f in remaining):
                    facts = _facts(cfg, cv_path=cv_path, cv_label=label, job=job)
                    llm_ans = agent_answer(llm, facts, remaining)
                answers = {**llm_ans, **det}  # deterministic answers always win
                filled, missed = 0, []
                for f in fields:
                    v = answers.get(str(f["apply_id"]))
                    _got = _fill_field(page, f, v)
                    if _got:
                        filled += 1
                    elif v not in (None, ""):
                        missed.append(f"{(f.get('label','') or '')[:26]}[{f['kind']}]={str(v)[:24]}")
                    # stream the decision so you can judge the brain live, field by field
                    if v not in (None, ""):
                        store.log_apply_event(
                            run_id=RUN_ID, url=job.get("url", ""), company=job.get("company", ""),
                            role_title=job.get("title", ""), kind="field",
                            field=(f.get("label") or "")[:180], answer=v,
                            origin=("profile" if str(f["apply_id"]) in det else "ai"), ok=bool(_got))
                # ensure any consent / agree checkbox is ticked even if everything else missed
                for f in fields:
                    if f["kind"] == "checkbox" and any(x in (f.get("label", "").lower())
                                                       for x in ("consent", "agree", "gdpr", "privacy", "retain", "terms")):
                        if _fill_field(page, f, "yes"):
                            filled += 1
                print(f"    fields={len(fields)} · from profile={len(det)} · from AI={len(llm_ans)} · "
                      f"CV {'attached' if uploaded else 'NOT attached'}")
                print(f"    ✓ filled {filled}/{len(fields)} fields.")
                snap(f"2 · Filled {filled}/{len(fields)} fields",
                     f"CV {label} {'attached' if uploaded else 'NOT attached'} · "
                     f"profile={len(det)} AI={len(llm_ans)}")
                if missed:
                    print("      could not set: " + "; ".join(missed[:8]))
                # location depends on a live geocode dropdown; if it didn't commit, never submit empty
                loc_missed = any("location" in m.lower() for m in missed)
                if loc_missed:
                    print("    ⚠️ 'Current location' didn't auto-select (geocode dropdown slow/empty). "
                          "Type your city in the box and PICK it from the dropdown.")
                    if do_submit and not args.headless:
                        pause("       Do that in the open browser, then press Enter to continue... ")
                time.sleep(1)
                # CAPTCHA: I do NOT solve these (it defeats the site's bot protection). If one is
                # present, pause so YOU can solve it in the visible browser, then continue.
                # CAPTCHA: never auto-solved. Unattended → flag it for you and DON'T submit.
                blocked = _has_captcha(page)
                if blocked:
                    print("    🔒 CAPTCHA / bot-check detected on this form.")
                    if interactive and not args.headless:
                        pause("       Please solve the CAPTCHA in the open browser window, then press Enter... ")
                        blocked = _has_captcha(page)
                    else:
                        print("       Unattended run — leaving this one for you to finish manually.")
                if do_submit and not blocked:
                    submitted = submit_form(page)
                    print("    " + ("🚀 submitted." if submitted else "! submit button not found."))
                    time.sleep(3)
                    snap("3 · After submit", "submitted" if submitted else "submit button not found")
                elif blocked:
                    snap("3 · Blocked by CAPTCHA", "left for you to finish manually")
                elif not do_submit:
                    pause("    Review, then press Enter to record it (submit yourself first if you want)... ")
            except Exception as exc:
                print(f"    ! error: {str(exc)[:120]}")
                if not do_submit:
                    pause("    Press Enter when done... ")
            shot = str(shot_dir / f"{(job.get('company') or 'co').replace('/', '_')}_{int(time.time())}.png")
            img = None
            try:
                page.screenshot(path=shot, full_page=True); img = pathlib.Path(shot).read_bytes()
            except Exception:
                pass
            cap = f"{'✅ Applied' if (submitted or not do_submit) else '⚠️ Needs submit'}: {job.get('title')} — {job.get('company')}\nCV: {label}\n{job.get('url')}"
            if all(tg) and img:
                ok, det = notify.send_photo(tg[0], tg[1], shot, cap)
                print(f"    telegram: {'sent' if ok else det}")
            if blocked:
                outcome = "needs_manual"          # CAPTCHA — you finish this one; never auto-solved
            elif submitted:
                outcome = "submitted"
            elif not do_submit and interactive:
                outcome = "submitted"             # you reviewed + submitted it yourself during the pause
            else:
                # UNATTENDED fill-only = a DRY RUN. The browser closes, so nothing was sent —
                # never mark these applied, or you'd lose track of real applications.
                outcome = "needs_submit"
            # log every REAL application (a scored queued job OR a from-queue URL); skip only --url tests
            if job.get("dedupe_key") or req_id:
                try:
                    store.log_apply(dedupe_key=job.get("dedupe_key", ""), company=job.get("company", ""),
                                    role_title=job.get("title", ""), country=job.get("country", ""),
                                    url=job.get("url", ""), cv=label, status=outcome, screenshot=img)
                    if job.get("dedupe_key") and outcome == "submitted":
                        store.set_status(job["dedupe_key"], "applied")   # only when really submitted
                    trk.add(company=job.get("company", ""), role_title=job.get("title", "") or "Application",
                            country=job.get("country", "United Kingdom"), source_url=job.get("url", ""),
                            status="applied", applied_date=dt.date.today(), notes=f"CV: {label}")
                except Exception as exc:
                    print(f"    ! log error: {str(exc)[:80]}")
            else:
                print("    (test mode — not logged to DB/tracker)")
            store.log_apply_event(run_id=RUN_ID, url=job.get("url", ""), company=job.get("company", ""),
                                  role_title=job.get("title", ""), kind="job_end",
                                  field=f"{filled} field(s) filled", answer=outcome, origin="auto",
                                  ok=(outcome == "submitted"))
            if req_id:                        # mark the Streamlit-queued request done/needs-submit
                try:
                    _rs = {"submitted": "done", "needs_manual": "needs_manual"}.get(outcome, "needs_submit")
                    store.set_apply_request_status(req_id, _rs, f"CV {label} · filled {filled} fields")
                except Exception:
                    pass
            try:
                page.close()
            except Exception:
                pass
        try:
            browser.close()
        except Exception:
            pass
        # Steel bills per session-minute — ALWAYS release, even if the run errored.
        if steel_client is not None and steel_session is not None:
            try:
                steel_client.sessions.release(steel_session.id)
                print("Steel session released.")
            except Exception as exc:
                print(f"! could not release Steel session: {str(exc)[:100]}")
    store.log_apply_event(run_id=RUN_ID, kind="run_end", field="finished",
                          answer=f"{len(queue)} application(s) processed", origin="auto")
    store.close(); trk.close()
    if live_url:
        print(f"\n📺 Session replay: {live_url}")
    print("\nDone. Dashboard → Apply Queue → Apply Activity for the log + screenshots.")


if __name__ == "__main__":
    main()
