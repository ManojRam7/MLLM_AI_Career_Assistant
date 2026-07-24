"""Load the target-company bucket list (two tiers: top100 > master) and match
employer names to it. Matching is alphanumeric-normalised + substring, so 'Barclays'
matches 'Barclays UK' and 'Lloyds Banking Group' matches 'Lloyds'."""
from __future__ import annotations

import csv
import random
import re
from pathlib import Path

_STOP = {"uk", "ltd", "limited", "plc", "group", "europe", "international", "the",
         "holdings", "co", "company", "services", "solutions"}


def _core(s: str) -> str:
    """Normalised name with trailing stop-words (UK, Group, Ltd...) removed. Strips punctuation
    WITHIN words too, so Moody's -> moodys, Rolls-Royce -> rollsroyce, Checkout.com -> checkoutcom
    (otherwise apostrophe/dot/hyphen names normalise to empty and never match)."""
    s = (s or "").lower().replace("&", " ")
    words = [re.sub(r"[^a-z0-9]", "", w) for w in s.split()]
    words = [w for w in words if w]
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


def list_sectors(path: str | Path) -> list[str]:
    """Unique sector names in file order (the 7 Master List sheets)."""
    p = Path(path)
    if not p.exists():
        return []
    seen: list[str] = []
    with p.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            s = (row.get("sector") or "").strip()
            if s and s not in seen:
                seen.append(s)
    return seen


def load_company_sectors(path: str | Path) -> dict[str, str]:
    """{normalised_company: sector} for tagging each job with its Master-List sector."""
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    with p.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            c, sec = _core(row.get("company_name") or ""), (row.get("sector") or "").strip()
            if c and sec and c not in out:
                out[c] = sec
    return out


def company_sector(company: str, mapping: dict[str, str]) -> str:
    """Return the Master-List sector for an employer name, or '' if not a target company."""
    c = _core(company)
    if not c or not mapping:
        return ""
    if c in mapping:
        return mapping[c]
    for n, s in mapping.items():
        if len(n) >= 5 and (n in c or c in n):
            return s
    return ""


def companies_in_sector(path: str | Path, sector: str | None = None) -> list[tuple[str, str]]:
    """[(company_name, careers_url)] optionally filtered to one sector."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[tuple[str, str]] = []
    with p.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("company_name") or "").strip()
            url = (row.get("careers_url") or "").strip()
            if not name:
                continue
            if sector and (row.get("sector") or "").strip().lower() != sector.strip().lower():
                continue
            out.append((name, url))
    return out


def sample_top_companies(path: str | Path, n: int = 5, sector: str | None = None) -> list[str]:
    """A random sample of target company names for rotating per-run search. Filters to a
    sector when given; prefers tier=top100 if the file has it, else samples from all."""
    p = Path(path)
    if not p.exists():
        return []
    tops, allc = [], []
    with p.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("company_name") or "").strip()
            if not name:
                continue
            if sector and (row.get("sector") or "").strip().lower() != sector.strip().lower():
                continue
            allc.append(name)
            if (row.get("tier") or "").strip() == "top100":
                tops.append(name)
    pool = tops or allc
    return random.sample(pool, min(n, len(pool))) if pool else []
