"""Score a candidate's CV against a job description (LLM reasoning, not keyword count)."""
from __future__ import annotations

import json
import re

from ..models import FitResult
from .client import LLM, LLMError

SYSTEM = (
    "You are a meticulous UK data-science recruiter. Judge how well the candidate's "
    "real experience fits the job. Be sceptical: flag ghost/scam postings and roles "
    "that are too senior. Score 0-100 (0=irrelevant, 100=ideal). Be honest about gaps. "
    "Return JSON only."
)


def _to_result(d: dict) -> FitResult:
    return FitResult(
        score=int(d.get("score", 0) or 0),
        band=str(d.get("band", "")),
        reasoning=str(d.get("reasoning", "")),
        ghost_flag=bool(d.get("ghost_flag", False)),
        gaps=list(d.get("gaps", []) or []),
    )


def _extract_array(text: str) -> list:
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    s, e = t.find("["), t.rfind("]")
    if s >= 0 and e > s:
        try:
            return json.loads(t[s:e + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError("model did not return a JSON array")


def _candidate(base_cv: dict) -> str:
    return (f"CANDIDATE (real experience):\n{json.dumps(base_cv.get('profile_facts'))}\n"
            f"Skills: {json.dumps(base_cv.get('skills'))}\n\n")


def score_fit(llm: LLM, base_cv: dict, job: dict) -> FitResult:
    user = (
        _candidate(base_cv) +
        f"JOB:\nTitle: {job.get('title')}\nCompany: {job.get('company')}\n"
        f"Location: {job.get('location')}\nDescription:\n{(job.get('description') or '')[:2000]}\n\n"
        'Return JSON: {"score": int, "band": "High|Medium|Low", "reasoning": "2 sentences", '
        '"ghost_flag": bool, "gaps": ["..."]}'
    )
    return _to_result(llm.complete_json(SYSTEM, user))


def score_fit_batch(llm: LLM, base_cv: dict, jobs: list[dict]) -> dict[int, FitResult]:
    """Score several jobs in ONE LLM call (far fewer calls + tokens than one-by-one,
    so we stay inside free-tier rate limits). Returns {chunk_index: FitResult} for the
    jobs the model actually returned; any it omits stay unscored and retry next run."""
    if not jobs:
        return {}
    blocks = []
    for i, job in enumerate(jobs):
        blocks.append(f"[{i}] Title: {job.get('title')} | Company: {job.get('company')} | "
                      f"Location: {job.get('location')}\n{(job.get('description') or '')[:1200]}")
    user = (
        _candidate(base_cv) +
        "JOBS - score each independently against the candidate:\n\n" + "\n\n".join(blocks) +
        '\n\nReturn ONLY a JSON array, one object per job, exactly: '
        '[{"i": <job number>, "score": int, "band": "High|Medium|Low", '
        '"reasoning": "2 sentences", "ghost_flag": bool, "gaps": ["..."]}]'
    )
    arr = _extract_array(llm.complete(SYSTEM + "\nReturn ONLY a JSON array, no prose, no code fences.", user))
    out: dict[int, FitResult] = {}
    for obj in arr:
        if not isinstance(obj, dict):
            continue
        try:
            i = int(obj.get("i"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(jobs):
            out[i] = _to_result(obj)
    return out
