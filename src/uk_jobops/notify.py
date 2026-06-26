"""Optional digest notification (Telegram) + a local digest file."""
from __future__ import annotations

from pathlib import Path


def write_digest(rows: list[dict], out: str = "output/digest.md") -> str:
    lines = ["# New high-fit roles\n"]
    for r in rows:
        lines.append(f"- **{r.get('title')}** at {r.get('company')} ({r.get('location')}) "
                     f"- fit {r.get('fit_score')} - {r.get('url')}")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text("\n".join(lines), encoding="utf-8")
    return out


def send_telegram(rows: list[dict], token: str, chat_id: str) -> bool:
    if not (token and chat_id and rows):
        return False
    try:
        import requests

        text = "New high-fit roles:\n" + "\n".join(
            f"- {r.get('title')} @ {r.get('company')} (fit {r.get('fit_score')}) {r.get('url')}" for r in rows[:15])
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=20)
        return True
    except Exception:
        return False
