"""Role + seniority filtering: keep beginner/associate/mid IC data-science roles."""
from __future__ import annotations

import re

from .models import Job


def _word(term: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(term) + r"\b", re.I)


_SPAM = re.compile(
    r"\b(apprentice\w*|apprenticeship|placement programme|placement scheme|no experience needed|"
    r"bootcamp|boot camp|kickstart|work experience|self[-\s]?paced|traineeship|"
    r"course|academy|re[-\s]?train)\b", re.I)


def classify(title: str, include: list[str], exclude_title: list[str]) -> tuple[bool, str]:
    """Return (is_target, seniority_label). Decisions are made on the TITLE only."""
    t = title or ""
    # drop course/apprenticeship/placement spam outright
    if _SPAM.search(t):
        return False, "spam"
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


_RECRUITER = re.compile(
    r"\b(recruit\w*|staffing|resourc\w*|rpo|headhunt\w*|talent solutions|"
    r"talent acquisition|executive search|search partners|consultants?|agency)\b", re.I)

# Agencies rarely name themselves; they give themselves away in the JD ("our client...").
# Kept to strong signals only so a direct employer saying "a leading bank" is NOT dropped.
_AGENCY_DESC = re.compile(
    r"\b(our client is|on behalf of (?:our|a|the) client|recruiting (?:for|on behalf of) (?:our|a) client|"
    r"my client|client is (?:a|an|seeking|looking)|working (?:with|on behalf of) (?:our|a) client)\b", re.I)


def is_agency(company: str, description: str) -> bool:
    return bool(_RECRUITER.search(company or "") or _AGENCY_DESC.search(description or ""))

# THREE co-primary categories, decided on the title. Precedence (matches "data+AI mixed -> AI"):
#   1) AI/ML build roles      -> ai-engineer   (AI engineer, ML engineer, LLM/GenAI, NLP/CV, MLOps)
#   2) data-science           -> data scientist / applied / decision / research scientist
#   3) data-analysis          -> analyst / analytics / BI / insight / MI / reporting
_AI_CAT = re.compile(r"\b(ai engineer|a\.i\. engineer|ai/ml|ml engineer|mlops|machine learning engineer|"
                     r"machine learning|deep learning|\bllm\b|llms|gen[- ]?ai|generative ai|"
                     r"applied ai|ai scientist|nlp engineer|natural language|computer vision|"
                     r"ai developer|ai/ml engineer|ai research)\b", re.I)
_DS_CAT = re.compile(r"\b(data scientist|applied scientist|decision scientist|research scientist|"
                     r"data science|quantitative (?:analyst|researcher)|statistician)\b", re.I)
_DA_CAT = re.compile(r"\b(data analyst|data analytics|analytics engineer|business intelligence|"
                     r"bi analyst|bi developer|insight\w*|reporting analyst|mi analyst|"
                     r"analytics|analyst)\b", re.I)


def job_category(title: str) -> str:
    """Return 'ai-engineer' | 'data-science' | 'data-analysis' | '' from the job title."""
    t = title or ""
    if _AI_CAT.search(t):
        return "ai-engineer"
    if _DS_CAT.search(t):
        return "data-science"
    if _DA_CAT.search(t):
        return "data-analysis"
    return ""


def apply_filters(jobs: list[Job], include: list[str], exclude_title: list[str],
                  exclude_company: list[str] | None = None,
                  exclude_recruiters: bool = True) -> tuple[list[Job], list[Job]]:
    """Split into (targets, rejected). Drops recruitment agencies so only DIRECT
    employers remain (the point of a bucket-list-driven search)."""
    excl_co = [c.lower() for c in (exclude_company or [])]
    targets, rejected = [], []
    for j in jobs:
        ok, label = classify(j.title, include, exclude_title)
        company = j.company or ""
        if ok and excl_co and any(c in company.lower() for c in excl_co):
            ok, label = False, "excluded-company"
        elif ok and exclude_recruiters and is_agency(company, j.description):
            ok, label = False, "recruiter"
        j.seniority = label
        j.category = job_category(j.title)
        j.is_target = ok
        (targets if ok else rejected).append(j)
    return targets, rejected
