"""Deterministic ATS / anti-AI-trace validator. This is what guarantees the rules,
independent of which model produced the text."""
from __future__ import annotations

import re

from ..models import TailoredCV

BANNED = [
    "leverage", "delve", "seamless", "robust", "cutting-edge", "passionate", "showcase",
    "realm", "tapestry", "elevate", "empower", "unlock", "pivotal", "testament",
    "ever-evolving", "game-changer", "fast-paced", "spearhead", "synergy", "holistic",
    "results-driven", "actionable insights", "self-starter who", "wealth of experience",
    "harness the", "harness our",   # cliche verb only; "evaluation/test harness" is legitimate
]
US_SPELLING = re.compile(r"\b\w*(optimiz|analyz|organiz|behavior|modeling|visualiz|utiliz|center|color)\w*\b", re.I)
PLACEHOLDER = re.compile(r"\[(?!Date\]|Company\]|Name\]|Job Title\]|your )[^\]]{2,}\]")


def _text(t: TailoredCV) -> str:
    parts = [t.job_title_line, t.profile, t.education, t.fit_reasoning]
    parts += [(s.get("label", "") + " " + s.get("items", "")) if isinstance(s, dict) else str(s) for s in t.skills]
    for e in t.experience:
        parts.append(e.get("title", "")); parts += e.get("bullets", [])
    parts += [p.get("name", "") + " " + p.get("text", "") for p in t.projects]
    parts += t.cover_letter
    return "\n".join(parts)


def keyword_coverage(t: TailoredCV) -> float:
    if not t.jd_keywords:
        return 1.0
    text = _text(t).lower()
    hit = sum(1 for k in t.jd_keywords if k.lower() in text)
    return round(hit / len(t.jd_keywords), 2)


def validate(t: TailoredCV) -> tuple[bool, list[str]]:
    text = _text(t)
    issues: list[str] = []
    if "—" in text or "–" in text:
        issues.append("Contains em/en dash - replace with comma or hyphen.")
    low = text.lower()
    for w in BANNED:
        if w in low:
            issues.append(f"Banned AI-trace phrase: '{w}'.")
    us = sorted({m.group(0) for m in US_SPELLING.finditer(text)})
    if us:
        issues.append(f"American spelling: {us} - use British spelling.")
    ph = PLACEHOLDER.findall(text)
    if ph:
        issues.append(f"Unfilled placeholder(s): {ph}.")
    cov = keyword_coverage(t)
    if cov < 0.8:
        issues.append(f"Keyword coverage {cov} < 0.80 - surface more genuine JD keywords in profile/skills.")
    if len(t.cover_letter) < 5:
        issues.append("Cover letter is incomplete (need greeting + 4 paragraphs + sign-off).")
    # 'critical' = anything that must be fixed (dashes, banned, placeholders, US spelling)
    critical = [i for i in issues if "dash" in i or "Banned" in i or "placeholder" in i or "American" in i]
    return (len(critical) == 0), issues
