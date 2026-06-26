"""Role + seniority filtering: keep beginner/associate/mid IC data-science roles."""
from __future__ import annotations

import re

from .models import Job


def _word(term: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(term) + r"\b", re.I)


def classify(title: str, include: list[str], exclude_title: list[str]) -> tuple[bool, str]:
    """Return (is_target, seniority_label). Decisions are made on the TITLE only."""
    t = title or ""
    # exclude senior/lead/manager-only roles (whole word, on the title)
    for term in exclude_title:
        if _word(term).search(t):
            return False, "excluded-senior"
    # must match a target role keyword on the title
    if not any(_word(term).search(t) for term in include):
        return False, "off-target"
    if re.search(r"\b(junior|graduate|entry[-\s]*level|intern)\b", t, re.I):
        return True, "beginner"
    if re.search(r"\b(associate|mid[-\s]*level|ii|level\s*2)\b", t, re.I):
        return True, "associate/mid"
    return True, "data-scientist"


def apply_filters(jobs: list[Job], include: list[str], exclude_title: list[str]) -> tuple[list[Job], list[Job]]:
    """Split into (targets, rejected)."""
    targets, rejected = [], []
    for j in jobs:
        ok, label = classify(j.title, include, exclude_title)
        j.seniority = label
        j.is_target = ok
        (targets if ok else rejected).append(j)
    return targets, rejected
