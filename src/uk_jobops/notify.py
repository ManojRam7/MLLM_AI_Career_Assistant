"""Telegram job alerts (rich, per-job, with recommendation levels) + a local digest file.
Send functions return diagnostics so the pipeline can surface *why* Telegram failed."""
from __future__ import annotations

import html
import re
from pathlib import Path


def _e(s) -> str:
    return html.escape(str(s or ""))


# Government / public-sector employers always qualify for alerts (independent of the allowlist).
_GOV = re.compile(r"\b(nhs|hmrc|dwp|dvla|dvsa|gov\.uk|government|civil service|home office|"
                  r"cabinet office|ministry|department for|council|borough|county|university|"
                  r"ordnance survey|met office|environment agency|ofgem|ofcom|police|"
                  r"national health|public health|hm revenue)\b", re.I)


def load_notify_allowlist(path: str) -> list[str]:
    """Load the curated top-employer allowlist (one name per line, '#' comments ignored)."""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())


def is_top_or_gov(company: str, sector: str, allowlist: list[str], gov_sectors: list[str]) -> bool:
    """True if this employer is a curated top company, or government (by sector or name)."""
    if sector and any(sector.strip().lower() == g.strip().lower() for g in (gov_sectors or [])):
        return True
    if _GOV.search(company or ""):
        return True
    c = _norm(company)
    if not c:
        return False
    for entry in allowlist:
        e = _norm(entry)
        if not e:
            continue
        # whole-word / phrase match either direction ("Three" ~ "Three UK", "Goldman Sachs" ~ "Goldman Sachs Intl")
        if re.search(rf"\b{re.escape(e)}\b", c) or re.search(rf"\b{re.escape(c)}\b", e):
            return True
    return False


def write_digest(rows: list[dict], out: str = "output/digest.md") -> str:
    lines = ["# New high-fit roles\n"]
    for r in rows:
        lines.append(f"- **{r.get('title')}** at {r.get('company')} ({r.get('location')}) "
                     f"- fit {r.get('fit_score')} - {r.get('url')}")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text("\n".join(lines), encoding="utf-8")
    return out


def _level(fit: int) -> tuple[str, str]:
    if fit >= 85:
        return "🎯", "Highly recommended for you"
    if fit >= 75:
        return "👍", "Strong match"
    return "🔎", "Worth a look"


def _message(r: dict, name: str) -> str:
    """HTML-formatted alert (HTML mode avoids Markdown parse errors from '_' in tracking URLs)."""
    fit = int(r.get("fit_score") or 0)
    emoji, level = _level(fit)
    if r.get("bucket_tier") == "top100":
        tag = "  ⭐ <b>Top-100 target company</b>"
    elif r.get("in_bucket"):
        tag = "  ⭐ <b>target company</b>"
    else:
        tag = ""
    reason = _e((r.get("fit_reasoning") or "").split(". ")[0]).rstrip(".")
    loc = _e(r.get("locations") or r.get("location") or "")
    when = str(r.get("posted_date") or r.get("first_seen_at") or "")[:10]
    sector = _e(r.get("sector") or "")
    parts = [f"{emoji} <b>{_e(level)}, {_e(name)}!</b>",
             f"<b>{_e(r.get('title'))}</b> — {_e(r.get('company'))}",
             f"📊 Fit <b>{fit}/100</b>{tag}"]
    meta = " · ".join(x for x in [f"📍 {loc}" if loc else "", f"🗓 {when}" if when else "",
                                  f"🏷 {sector}" if sector else ""] if x)
    if meta:
        parts.append(meta)
    if reason:
        parts.append(f"✅ {reason}.")
    if r.get("url"):
        u = _e(r.get("url"))
        parts.append(f'🔗 <a href="{u}">open job</a>')
    return "\n".join(parts)


def send_message(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """Send one Telegram message. Returns (ok, detail) so callers can report errors."""
    if not (token and chat_id):
        return False, "no token/chat_id"
    import requests

    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=20)
        if r.status_code == 200:
            return True, "ok"
        try:
            desc = r.json().get("description", r.text[:160])
        except Exception:
            desc = r.text[:160]
        return False, f"{r.status_code} {desc}"
    except Exception as exc:
        return False, str(exc)[:160]


def send_job_alerts(rows: list[dict], token: str, chat_id: str, name: str = "there") -> tuple[int, str]:
    """Send one rich message per job. Returns (count_sent, first_error)."""
    if not (token and chat_id and rows):
        return 0, ""
    sent, err = 0, ""
    for r in rows:
        ok, detail = send_message(token, chat_id, _message(r, name))
        if ok:
            sent += 1
        elif not err:
            err = detail
    return sent, err
