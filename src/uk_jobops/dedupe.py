"""Fuzzy, host-agnostic de-duplication so the same role across boards collapses to one."""
from __future__ import annotations

from .models import Job, _norm


def _best(a: Job, b: Job) -> Job:
    """Prefer the record with a description, then a real URL, then an ATS source."""
    score = lambda j: (len(j.description), len(j.url), j.source == "Company ATS")
    return a if score(a) >= score(b) else b


def dedupe(jobs: list[Job]) -> list[Job]:
    try:
        from rapidfuzz import fuzz
    except Exception:  # pragma: no cover - fall back to exact key only
        fuzz = None

    kept: list[Job] = []
    for job in jobs:
        match_idx = -1
        for i, k in enumerate(kept):
            if _norm(job.company) != _norm(k.company):
                continue
            same_loc = _norm(job.location).split(",")[0] == _norm(k.location).split(",")[0]
            if fuzz is None:
                title_match = _norm(job.title) == _norm(k.title)
            else:
                title_match = fuzz.token_sort_ratio(job.title, k.title) >= 90
            if title_match and (same_loc or not job.location or not k.location):
                match_idx = i
                break
        if match_idx >= 0:
            winner = _best(job, kept[match_idx])
            winner.first_seen_at = min(job.first_seen_at, kept[match_idx].first_seen_at)
            kept[match_idx] = winner
        else:
            kept.append(job)
    return kept
