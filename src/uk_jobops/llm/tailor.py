"""Rulebook-driven CV + cover-letter tailoring: draft -> critique -> repair -> validate.

Includes a robust coercion layer so the pipeline never crashes on model-shape
variation (e.g. skills returned as plain strings instead of {label, items})."""
from __future__ import annotations

import json
from typing import Any

from ..models import TailoredCV
from .client import LLM, LLMError
from .validator import keyword_coverage, validate


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _num(v: Any, cast, default):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def _norm_skills(v: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if isinstance(v, dict):
        for label, items in v.items():
            out.append({"label": str(label), "items": ", ".join(map(str, items)) if isinstance(items, list) else str(items)})
        return out
    for it in _as_list(v):
        if isinstance(it, dict):
            label = str(it.get("label") or it.get("category") or it.get("name") or "")
            items = it.get("items") or it.get("skills") or it.get("value") or ""
            out.append({"label": label, "items": ", ".join(map(str, items)) if isinstance(items, list) else str(items)})
        else:
            s = str(it)
            lbl, sep, rest = s.partition(":")
            out.append({"label": lbl.strip(), "items": rest.strip()} if sep else {"label": "", "items": s})
    return out


def _norm_experience(v: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in _as_list(v):
        if isinstance(it, dict):
            bullets = it.get("bullets") or it.get("points") or it.get("description") or []
            out.append({"title": str(it.get("title") or it.get("role") or ""),
                        "dates": str(it.get("dates") or it.get("date") or ""),
                        "bullets": [str(b) for b in _as_list(bullets)]})
        else:
            out.append({"title": "", "dates": "", "bullets": [str(it)]})
    return out


def _norm_projects(v: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for it in _as_list(v):
        if isinstance(it, dict):
            out.append({"name": str(it.get("name") or it.get("title") or ""),
                        "text": str(it.get("text") or it.get("description") or "")})
        else:
            out.append({"name": "", "text": str(it)})
    return out


def _edu_to_str(v: Any) -> str:
    if isinstance(v, str):
        return v
    parts = []
    for it in _as_list(v):
        parts.append(f"{it.get('degree', '')} ({it.get('meta', '')})" if isinstance(it, dict) else str(it))
    return "; ".join(parts)


def _norm_cover(v: Any) -> list[str]:
    if isinstance(v, str):
        return [p.strip() for p in v.split("\n\n") if p.strip()]
    return [str(x) for x in _as_list(v)]


def _to_tailored(d: dict) -> TailoredCV:
    return TailoredCV(
        job_title_line=str(d.get("job_title_line") or d.get("title") or ""),
        profile=str(d.get("profile") or ""),
        skills=_norm_skills(d.get("skills")),
        experience=_norm_experience(d.get("experience")),
        projects=_norm_projects(d.get("projects")),
        education=_edu_to_str(d.get("education")),
        cover_letter=_norm_cover(d.get("cover_letter")),
        jd_keywords=[str(k) for k in _as_list(d.get("jd_keywords"))],
        keyword_coverage=_num(d.get("keyword_coverage"), float, 0.0),
        gaps=[str(g) for g in _as_list(d.get("gaps"))],
        fit_score=_num(d.get("fit_score"), int, 0),
        fit_reasoning=str(d.get("fit_reasoning") or ""),
    )


def _user_prompt(base_cv: dict, job: dict, profile: dict | None = None) -> str:
    prof = (f"CANDIDATE PREFERENCES (emphasise these truthfully, never invent):\n{json.dumps(profile)}\n\n"
            if profile else "")
    return (
        "BASE CV (the ONLY source of truth - never invent beyond this):\n"
        f"{json.dumps(base_cv, ensure_ascii=False)}\n\n"
        + prof +
        "JOB TO TAILOR FOR:\n"
        f"Title: {job.get('title')}\nCompany: {job.get('company')}\n"
        f"Location: {job.get('location')}\nDescription:\n{(job.get('description') or '')[:2500]}\n\n"
        "Follow the rulebook exactly and return the JSON it specifies. "
        "skills must be a list of objects with 'label' and 'items'."
    )


def tailor(llm: LLM, rulebook: str, base_cv: dict, job: dict, *, max_repair: int = 1,
           profile: dict | None = None) -> TailoredCV:
    system = rulebook
    user = _user_prompt(base_cv, job, profile)
    tp, tm = llm.tailor_provider, llm.tailor_model
    data = llm.complete_json(system, user, provider=tp, model=tm)   # 1) primary draft
    tailored = _to_tailored(data)
    ok, issues = validate(tailored)

    if not ok and max_repair > 0:
        critic_notes = ""
        if llm.available(llm.critic):
            try:                                       # 2) second model critiques
                critic_notes = llm.complete(
                    "You are a strict UK ATS reviewer. List concrete one-line corrections only.",
                    f"Rulebook issues found: {issues}\nTailored CV JSON:\n{json.dumps(data)[:4000]}",
                    provider=llm.critic, temperature=0.0)
            except LLMError:
                critic_notes = ""
        repair_user = user + (
            "\n\nYour previous output failed these checks - FIX ALL and return corrected JSON:\n"
            + "\n".join("- " + i for i in issues) + ("\n" + critic_notes if critic_notes else ""))
        try:                                           # 3) primary revises
            data = llm.complete_json(system, repair_user, provider=tp, model=tm)
            tailored = _to_tailored(data)
        except LLMError:
            pass

    tailored.keyword_coverage = keyword_coverage(tailored)
    return tailored
