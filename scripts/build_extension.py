"""Generate extension/profile.json from your applicant brain.

The Chrome extension fills forms in YOUR browser (so Workday/iCIMS/LinkedIn work — you're already
signed in). It needs the same facts the Python agent uses, so this script flattens
data/applicant_profile.yaml + base_cv.json into one JSON the extension bundles.

Run after editing your profile:
    python scripts/build_extension.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from uk_jobops.config import load_config  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "extension" / "profile.json"


def main() -> None:
    cfg = load_config()
    from uk_jobops import applicant

    prof = applicant.load_profile(cfg)
    key = applicant.answer_key(cfg)          # the exact same canonical facts the agent uses

    payload = {
        "answers": key,                       # name/email/phone/visa/salary/diversity/address/...
        "question_bank": prof.get("question_bank", []),
        "voice": prof.get("voice", ""),
        "summary": prof.get("summary", ""),
        "honest_gaps": prof.get("honest_gaps", ""),
        "experience": prof.get("experience", []),
        "education": prof.get("education", []),
        "certifications": prof.get("certifications", []),
        "work_authorization": prof.get("work_authorization", {}),
        "background_check": prof.get("background_check", {}),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"✓ wrote {OUT.relative_to(ROOT)}  ({len(json.dumps(payload))} bytes)")
    print(f"  {len(payload['answers'])} standard answers · {len(payload['question_bank'])} banked questions")
    print("\nNow load it in Chrome:  chrome://extensions → Developer mode → Load unpacked → select the "
          "`extension/` folder. Re-run this script and hit Reload whenever you edit your profile.")


if __name__ == "__main__":
    main()
