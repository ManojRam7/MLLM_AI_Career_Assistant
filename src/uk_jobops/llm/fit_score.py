"""Score a candidate's CV against a job description (LLM reasoning, not keyword count)."""
from __future__ import annotations

import json
import re

from ..models import FitResult
from .client import LLM, LLMError

SYSTEM = (
    "You are a STRICT, honest UK data recruiter scoring data-science, AI-engineer and data-analysis roles. "
    "Score how well the candidate's REAL, EVIDENCED experience matches the job's requirements TODAY - not "
    "their potential after retraining. Reward direct evidence; do NOT inflate the score for merely "
    "transferable or adjacent skills.\n"
    "METHOD: (1) extract the job's HARD / MUST-HAVE requirements - the named tools, platforms and techniques "
    "the JD treats as required or 'mostly' used; (2) check each against the candidate's actual evidence; "
    "(3) a missing hard requirement is a genuine gap that CAPS the score - a role built 'mostly on BigQuery' "
    "cannot score high for a candidate with no BigQuery. Adjacent tools (Azure vs BigQuery, Looker Studio vs "
    "Looker, Power BI vs Tableau) are PARTIAL credit, never full.\n"
    "CALIBRATION (stay strict): 85-100 = meets nearly ALL hard requirements with direct evidence; 70-84 = most "
    "hard requirements met with a few partial; 55-69 = several core requirements only transferable/partial or "
    "missing; 40-54 = weak, multiple hard gaps; <40 = off-target. Most 'good but not a direct stack match' "
    "roles are 60-72, NOT 85.\n"
    "HARD RULE 1 (LOCATION): the candidate can ONLY work in the United Kingdom. If the ROLE location is outside "
    "the UK (India, USA, Canada, France, Germany, Spain, Portugal, Ireland, Dubai/UAE, Poland, Singapore, "
    "Australia, etc.), score <= 15 and ghost_flag=true - even if the company is UK-based.\n"
    "HARD RULE 2 (EXPIRED): expired/closed/filled/removed/'no longer accepting', or posted more than ~2 months "
    "ago -> score <= 15 and ghost_flag=true.\n"
    "HARD RULE 3 (AGENCY): recruitment/staffing agency, or a description posted on behalf of an unnamed 'client' "
    "-> score <= 40 and ghost_flag=true. Also ghost_flag vague reposts with no named employer or responsibilities.\n"
    "In 'gaps', NAME the specific missing hard requirements (e.g. 'BigQuery', 'Looker', 'dbt', 'dimensional "
    "modelling'). In 'reasoning', give the strict current-match verdict AND the realistic score achievable "
    "after HONEST tailoring (no invented skills), e.g. 'Strict match 66; ~76 after honest tailoring.' "
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


def _candidate(base_cv: dict, profile: dict | None = None) -> str:
    s = (f"CANDIDATE (real experience):\n{json.dumps(base_cv.get('profile_facts'))}\n"
         f"Skills: {json.dumps(base_cv.get('skills'))}\n")
    if profile:
        s += f"\nCANDIDATE PREFERENCES & SCORING RUBRIC (judge the fit against this):\n{json.dumps(profile)}\n"
    return s + "\n"


def score_fit(llm: LLM, base_cv: dict, job: dict, profile: dict | None = None) -> FitResult:
    user = (
        _candidate(base_cv, profile) +
        f"JOB:\nTitle: {job.get('title')}\nCompany: {job.get('company')}\n"
        f"Location: {job.get('location')}\nDescription:\n{(job.get('description') or '')[:2000]}\n\n"
        'Return JSON: {"score": int, "band": "High|Medium|Low", "reasoning": "strict verdict + named gaps + ~potential after honest tailoring", '
        '"ghost_flag": bool, "gaps": ["..."]}'
    )
    return _to_result(llm.complete_json(SYSTEM, user, provider=llm.score_provider, model=llm.score_model))


def score_fit_batch(llm: LLM, base_cv: dict, jobs: list[dict], profile: dict | None = None,
                    provider: str | None = None, model: str | None = None) -> dict[int, FitResult]:
    """Score several jobs in ONE LLM call (far fewer calls + tokens than one-by-one,
    so we stay inside free-tier rate limits). Returns {chunk_index: FitResult} for the
    jobs the model actually returned; any it omits stay unscored and retry next run.
    `provider`/`model` override the default scorer (used for a second-opinion Gemini pass)."""
    if not jobs:
        return {}
    blocks = []
    for i, job in enumerate(jobs):
        blocks.append(f"[{i}] Title: {job.get('title')} | Company: {job.get('company')} | "
                      f"Location: {job.get('location')}\n{(job.get('description') or '')[:1200]}")
    user = (
        _candidate(base_cv, profile) +
        "JOBS - score each independently against the candidate:\n\n" + "\n\n".join(blocks) +
        '\n\nReturn ONLY a JSON array, one object per job, exactly: '
        '[{"i": <job number>, "score": int, "band": "High|Medium|Low", '
        '"reasoning": "strict verdict + named gaps + ~potential after honest tailoring", "ghost_flag": bool, "gaps": ["..."]}]'
    )
    arr = _extract_array(llm.complete(SYSTEM + "\nReturn ONLY a JSON array, no prose, no code fences.",
                                      user, provider=provider or llm.score_provider,
                                      model=model or llm.score_model))
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
