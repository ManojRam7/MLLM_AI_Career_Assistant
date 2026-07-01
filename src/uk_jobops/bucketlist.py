"""Load the target-company bucket list (two tiers: top100 > master) and match
employer names to it. Matching is alphanumeric-normalised + substring, so 'Barclays'
matches 'Barclays UK' and 'Lloyds Banking Group' matches 'Lloyds'."""
from __future__ import annotations

import csv
import random
from pathlib import Path

_STOP = {"uk", "ltd", "limited", "plc", "group", "europe", "international", "the",
         "holdings", "co", "company", "services", "solutions"}


def _core(s: str) -> str:
    """Normalised name with trailing stop-words (UK, Group, Ltd...) removed."""
    words = [w for w in (s or "").lower().replace("&", " ").split() if w.isalnum()]
    while words and words[-1] in _STOP:
        words.pop()
    return "".join(words)


def load_bucket_tiers(path: str | Path) -> dict[str, str]:
    """{normalised_company: tier} where tier is 'top100' or 'master' (top100 wins)."""
    p = Path(path)
    if not p.exists():
        return {}
    tiers: dict[str, str] = {}
    with p.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("company_name") or "").strip()
            tier = (row.get("tier") or "master").strip() or "master"
            c = _core(name)
            if c and (c not in tiers or tier == "top100"):
                tiers[c] = tier
    return tiers


def load_bucket_companies(path: str | Path) -> set[str]:
    return set(load_bucket_tiers(path))


def bucket_tier(company: str, tiers: dict[str, str]) -> str:
    """Return 'top100' | 'master' | '' for an employer name."""
    c = _core(company)
    if not c or not tiers:
        return ""
    if c in tiers:
        return tiers[c]
    best = ""
    for n, t in tiers.items():  # substring match; top100 wins
        if len(n) >= 5 and (n in c or c in n):
            if t == "top100":
                return "top100"
            best = "master"
    return best


def is_bucket(company: str, tiers) -> bool:
    if isinstance(tiers, dict):
        return bool(bucket_tier(company, tiers))
    return bool(bucket_tier(company, {c: "master" for c in tiers}))


def sample_top_companies(path: str | Path, n: int = 5) -> list[str]:
    """A random sample of top-100 company names, for rotating per-run targeted search."""
    p = Path(path)
    if not p.exists():
        return []
    tops: list[str] = []
    with p.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("tier") or "").strip() == "top100":
                name = (row.get("company_name") or "").strip()
                if name:
                    tops.append(name)
    return random.sample(tops, min(n, len(tops))) if tops else []
