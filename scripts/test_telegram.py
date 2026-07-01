"""Test the Telegram setup: sends a welcome message + a few recent job alerts.
Run locally (reads .env + Supabase):  python scripts/test_telegram.py"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from uk_jobops import notify  # noqa: E402
from uk_jobops.config import load_config  # noqa: E402


def main() -> None:
    cfg = load_config()
    tok, chat = cfg.secrets.telegram_bot_token, cfg.secrets.telegram_chat_id
    if not (tok and chat):
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in .env — add them and retry.")
        return

    import requests

    name = (cfg.settings.get("candidate", {}).get("name", "there") or "there").split()[0]
    welcome = (f"✅ *Job Search Assistant connected!*\nHi {name} — you'll get an alert here whenever "
               "a new high-fit role is found. Here are a few recent matches:")
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": welcome, "parse_mode": "Markdown"}, timeout=20)
    if r.status_code != 200:
        print(f"Telegram rejected the welcome message ({r.status_code}): {r.text[:300]}")
        print("Fix: confirm TELEGRAM_CHAT_ID is correct and that you've sent /start to your bot.")
        return
    print("Welcome message sent ✔")

    db_url = cfg.secrets.supabase_db_url
    if not db_url:
        print("No SUPABASE_DB_URL, so no job alerts to send (welcome worked though).")
        return
    from uk_jobops.db import Store

    store = Store(db_url)
    store.init_schema()
    rows = store.jobs_to_notify(min_fit=75, limit=5) or store.digest(min_fit=70, limit=5)
    store.close()
    if not rows:
        print("No high-fit jobs in the database yet to alert on.")
        return
    sent = notify.send_job_alerts(rows, tok, chat, name)
    print(f"Sent {sent} job alert(s) ✔  — check Telegram.")


if __name__ == "__main__":
    main()
