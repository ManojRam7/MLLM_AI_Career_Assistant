"""Run the full pipeline. Usage: python scripts/run_pipeline.py --mode recurring|first"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from uk_jobops.pipeline import run  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="recurring", choices=["first", "recurring"])
    args = ap.parse_args()
    print(json.dumps(run(args.mode), indent=2, default=str))


if __name__ == "__main__":
    main()
