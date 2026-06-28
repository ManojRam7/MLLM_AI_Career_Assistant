"""Fuzzy de-duplication. Two postings are the same job when the employer and title
match AND the description is (near) identical OR the location matches - so the same
role reposted across towns collapses to one row (with all its locations), while
genuinely different roles from the same employer stay separate."""
from __future__ import annotations

from .models import Job, _norm


def _best(a: Job, b: Job) -> Job:
    """Prefer the record with a description, then a real URL, then an ATS source."""
    score = lambda j: (len(j.description), len(j.url), j.source == "Company ATS")
    return a if score(a) >= score(b) else b


def _merge_locations(a: Job, b: Job) -> str:
    seen, out = set(), []
    for src in (a.locations, a.location, b.locations, b.location):
        for loc in (src or "").split(","):
            loc = loc.strip()
            key = loc.lower()
            if loc and key not in seen:
                seen.add(key)
                out.append(loc)
    return ", ".join(out)


def dedupe(jobs: list[Job]) -> list[Job]:
    try:
        from rapidfuzz import fuzz
    except Exception:  # pragma: no cover - fall back to exact comparisons
        fuzz = None

    kept: list[Job] = []
    for job in jobs:
        match_idx = -1
        for i, k in enumerate(kept):
            if _norm(job.company) != _norm(k.company):
                continue
            if fuzz is None:
                title_match = _norm(job.title) == _norm(k.title)
            else:
                title_match = fuzz.token_sort_ratio(job.title, k.title) >= 90
            if not title_match:
                continue
            # Same role => (near) identical description, even if reposted across towns
            # or sources. Different descriptions => different roles, so keep both. We only
            # fall back to "same town" when neither posting has a description to compare.
            ja, ka = _norm(job.description)[:300], _norm(k.description)[:300]
            if job.dedupe_key and job.dedupe_key == k.dedupe_key:
                same = True
            elif ja and ka:
                same = (ja == ka) or (fuzz is not None and fuzz.ratio(ja, ka) >= 92)
            else:
                # only when NEITHER has a description (can't compare) do we fall back to town
                same = (not ja and not ka and bool(job.location)
                        and _norm(job.location).split(",")[0] == _norm(k.location).split(",")[0])
            if same:
                match_idx = i
                break
        if match_idx >= 0:
            merged_locs = _merge_locations(job, kept[match_idx])
            winner = _best(job, kept[match_idx])
            winner.first_seen_at = min(job.first_seen_at, kept[match_idx].first_seen_at)
            winner.locations = merged_locs
            kept[match_idx] = winner
        else:
            job.locations = job.locations or job.location
            kept.append(job)
    return kept
