"""One-off (or occasional) AUDIT: follow every company's careers URL, detect its real ATS board,
and repoint careers_url to the canonical ATS URL in data/companies_master.csv.

Why: a company whose careers_url is a marketing page leans on Bright Data SERP (paid, ~1-day index
lag). If its page embeds/links a standard ATS board (Workday/Greenhouse/Lever/Ashby/SmartRecruiters/
Workable/Recruitee/Eightfold/Personio/Breezy/Teamtailor), we can pull that ATS's FREE public API
directly - real-time, zero credits, zero index lag. This converts as many companies as possible
from paid-SERP to free-ATS.

Uses only plain HTTP (no LLM tokens, no Bright Data credits). Run where there is outbound network
(GitHub Actions or locally): `python scripts/audit_ats.py`  (add --dry-run to preview only)."""
from __future__ import annotations

import argparse
import csv
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from uk_jobops.sources.ats import detect_ats, resolve_ats  # noqa: E402

CSV = pathlib.Path(__file__).resolve().parents[1] / "data" / "companies_master.csv"


def canonical_url(ats: str, token: str) -> str:
    """Rebuild the canonical ATS board URL from (ats, token) so future runs hit the API directly."""
    if ats == "workday":
        tenant, dc, site = token.split("|")
        return f"https://{tenant}.{dc}.myworkdayjobs.com/{site}"
    return {
        "greenhouse": f"https://boards.greenhouse.io/{token}",
        "lever": f"https://jobs.lever.co/{token}",
        "ashby": f"https://jobs.ashbyhq.com/{token}",
        "smartrecruiters": f"https://careers.smartrecruiters.com/{token}",
        "workable": f"https://apply.workable.com/{token}",
        "recruitee": f"https://{token}.recruitee.com",
        "eightfold": f"https://{token}.eightfold.ai",
        "personio": f"https://{token}.jobs.personio.com",
        "breezy": f"https://{token}.breezy.hr",
        "teamtailor": f"https://{token}.teamtailor.com",
    }.get(ats, "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, do not write the CSV")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
    fields = rows[0].keys() if rows else []

    def work(row):
        url = (row.get("careers_url") or "").strip()
        if not url:
            return row, None, "no-url"
        if detect_ats(url):
            return row, None, "already-ats"          # already a direct ATS board
        det = resolve_ats(url, follow=True)           # fetch the page, find an embedded ATS board
        if det:
            return row, canonical_url(*det), det[0]
        return row, None, "custom"                    # custom/anti-bot -> stays on SERP

    converted = already = custom = errs = 0
    changes = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [ex.submit(work, r) for r in rows]
        for f in as_completed(futs):
            row, new_url, kind = f.result()
            if kind == "already-ats":
                already += 1
            elif new_url:
                converted += 1
                changes.append((row["company_name"], kind, row["careers_url"], new_url))
                row["careers_url"] = new_url
            elif kind == "custom":
                custom += 1
            else:
                errs += 1

    print(f"\n=== ATS AUDIT ===\n{len(rows)} companies")
    print(f"  already on a direct ATS : {already}")
    print(f"  CONVERTED to free ATS   : {converted}")
    print(f"  custom/no-ATS (SERP)    : {custom}")
    print(f"  no url / error          : {errs}")
    for name, ats, old, new in sorted(changes):
        print(f"  + {name:32} [{ats}]  {new}")

    if changes and not args.dry_run:
        with CSV.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(fields))
            w.writeheader()
            w.writerows(rows)
        print(f"\nWrote {converted} repointed URLs to {CSV.name}. "
              f"That many companies now use FREE, real-time ATS APIs instead of paid SERP.")
    elif args.dry_run:
        print("\n(dry-run: no changes written)")


if __name__ == "__main__":
    main()
