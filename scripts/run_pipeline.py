"""Run the full pipeline.

Usage:
  python scripts/run_pipeline.py --mode recurring|first
  python scripts/run_pipeline.py --sector "Banking"      # one sector
  python scripts/run_pipeline.py --sector auto           # pick the sector by current UTC hour
  python scripts/run_pipeline.py --sector full           # every source, no sector focus
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from uk_jobops.config import ConfigError, load_config  # noqa: E402
from uk_jobops.pipeline import run  # noqa: E402


def _resolve_sector(arg: str | None, cfg) -> str | None:
    """Map the --sector argument to a concrete sector name (or None for a full run)."""
    rot = cfg.settings.get("rotation", {})
    sectors = rot.get("sectors", [])
    if not arg or arg.lower() == "full":
        return None
    if arg.lower() == "auto":
        if not (rot.get("enabled") and sectors):
            return None
        h = datetime.datetime.now(datetime.timezone.utc).hour
        broad_h = rot.get("broad_hour_utc")
        if broad_h is not None and h == broad_h:   # dedicated daily broad/full run
            return None
        hours = rot.get("schedule_hours_utc", [])[:len(sectors)]
        idx = None
        for i, sh in enumerate(hours):
            if h >= sh:
                idx = i
        if idx is None:           # off-schedule / before first sector slot -> full broad run
            return None
        return sectors[idx]
    for s in sectors:             # explicit name, case-insensitive
        if s.lower() == arg.lower():
            return s
    return arg                    # best-effort pass-through


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="recurring", choices=["first", "recurring"])
    ap.add_argument("--sector", default=None,
                    help="sector name, 'auto' (pick by UTC hour), or 'full' (no sector focus)")
    args = ap.parse_args()
    try:
        cfg = load_config()
        sector = _resolve_sector(args.sector, cfg)
        print(json.dumps(run(args.mode, sector), indent=2, default=str))
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
