"""Score a candidate's CV against a job description (LLM reasoning, not keyword count)."""
from __future__ import annotations

import json

from ..models import FitResult
from .client import LLM

SYSTEM = (
    "You are a meticulous UK data-science recruiter. Judge how well the candidate's "
    "real experience fits the job. Be sceptical: flag ghost/scam postings and roles "
    "that are too senior. Score 0-100 (0=irrelevant, 100=ideal). Be honest about gaps. "
    "Return JSON only."
)


def score_fit(llm: LLM, base_cv: dict, job: dict) -> FitResult:
    user = (
        f"CANDIDATE (real experience):\n{json.dumps(base_cv.get('profile_facts'))}\n"
        f"Skills: {json.dumps(base_cv.get('skills'))}\n\n"
        f"JOB:\nTitle: {job.get('title')}\nCompany: {job.get('company')}\n"
        f"Location: {job.get('location')}\nDescription:\n{(job.get('description') or '')[:2000]}\n\n"
        'Return JSON: {"score": int, "band": "High|Medium|Low", "reasoning": "2 sentences", '
        '"ghost_flag": bool, "gaps": ["..."]}'
    )
    d = llm.complete_json(SYSTEM, user)
    return FitResult(
        score=int(d.get("score", 0) or 0),
        band=str(d.get("band", "")),
        reasoning=str(d.get("reasoning", "")),
        ghost_flag=bool(d.get("ghost_flag", False)),
        gaps=list(d.get("gaps", []) or []),
    )
