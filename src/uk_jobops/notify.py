"""Telegram job alerts (rich, per-job, with recommendation levels) + a local digest file.
Send functions return diagnostics so the pipeline can surface *why* Telegram failed."""
from __future__ import annotations

import html
from pathlib import Path


def _e(s) -> str:
    return html.escape(str(s or ""))


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
    parts = [f"{emoji} <b>{_e(level)}, {_e(name)}!</b>",
             f"<b>{_e(r.get('title'))}</b> — {_e(r.get('company'))}",
             f"📊 Fit <b>{fit}/100</b>{tag}"]
    if loc:
        parts.append(f"📍 {loc}")
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
