"""Add a custom job to track. Example:
   python scripts/add_job.py --title "Data Scientist" --company "Acme" --url https://... """
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from uk_jobops.config import load_config  # noqa: E402
from uk_jobops.db import Store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--company", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--location", default="")
    ap.add_argument("--description", default="")
    ap.add_argument("--status", default="shortlisted")
    args = ap.parse_args()
    cfg = load_config()
    store = Store(cfg.secrets.supabase_db_url)
    store.init_schema()
    key = store.add_custom_job(title=args.title, company=args.company, url=args.url,
                               location=args.location, description=args.description, status=args.status)
    store.close()
    print(f"Added custom job {key}: {args.title} @ {args.company}")


if __name__ == "__main__":
    main()
