"""Daily Google sync (runs in GitHub Actions after the pipeline): push tracker interview /
next-action dates -> Google Calendar, and back up the tracker CSV -> Google Drive.

Cleanly no-ops if GOOGLE_SERVICE_ACCOUNT_JSON (service account) isn't configured, so it never
breaks the pipeline. The application tracker table is read-only here."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from uk_jobops import gsync  # noqa: E402
from uk_jobops.config import load_config  # noqa: E402
from uk_jobops.tracker import Tracker, rows_to_csv  # noqa: E402


def main() -> None:
    if not gsync.configured():
        print("[gsync] no Google service account set — skipped")
        return
    cfg = load_config()
    if not cfg.secrets.supabase_db_url:
        print("[gsync] no SUPABASE_DB_URL — skipped")
        return
    trk = Tracker(cfg.secrets.supabase_db_url)
    trk.init_schema()
    apps = trk.list_all()
    try:
        n, msg = gsync.sync_calendar(apps)
        print(f"[gsync] calendar: synced {n} event(s) ({msg})")
    except Exception as exc:  # noqa: BLE001
        print(f"[gsync] calendar failed: {str(exc)[:200]}")
    try:
        print(f"[gsync] drive backup: {gsync.backup_to_drive(rows_to_csv(apps))}")
    except Exception as exc:  # noqa: BLE001
        print(f"[gsync] drive failed: {str(exc)[:200]}")
    trk.close()


if __name__ == "__main__":
    main()
