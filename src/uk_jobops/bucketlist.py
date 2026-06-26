"""Load the target-company bucket list and match employer names to it.
Matching is alphanumeric-normalised + substring, so 'Barclays' matches
'Barclays UK' and 'Lloyds Banking Group' matches 'Lloyds'."""
from __future__ import annotations

import csv
from pathlib import Path

_STOP = {"uk", "ltd", "limited", "plc", "group", "europe", "international", "the",
         "holdings", "co", "company", "services", "solutions"}


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _core(s: str) -> str:
    """Normalised name with trailing stop-words (UK, Group, Ltd...) removed."""
    words = [w for w in (s or "").lower().replace("&", " ").split() if w.isalnum()]
    while words and words[-1] in _STOP:
        words.pop()
    return "".join(words)


def load_bucket_companies(path: str | Path) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    names: set[str] = set()
    with p.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("company_name") or "").strip()
            if name:
                names.add(_core(name))
    names.discard("")
    return names


def is_bucket(company: str, names: set[str]) -> bool:
    c = _core(company)
    if not c or not names:
        return False
    if c in names:
        return True
    # substring either direction, but only for distinctive (>=5 char) names
    return any(len(n) >= 5 and (n in c or c in n) for n in names)
