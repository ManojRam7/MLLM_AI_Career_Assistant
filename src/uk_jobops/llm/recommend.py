"""Per-job ATS tailoring recommendations + a cover letter (TEXT, no documents).

One LLM call returns: fit score, gaps, ATS keywords to add, section-by-section guidance on how
to rewrite each CV section to pass THIS employer's ATS, and a full cover letter in the
candidate's format. Rendered to markdown for the dashboard. Uses the quality tailor provider
with the profile rubric injected. Honest: it re-frames real experience, never invents it."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .client import LLM

SYSTEM = (
    "You are an expert UK CV writer and ATS-optimisation specialist. Given a candidate's real CV, "
    "their preferences, and one specific job, produce HIGH-END, honest tailoring guidance that helps "
    "the CV pass that employer's ATS and recruiter screen. NEVER invent experience the candidate does "
    "not have - only re-frame, re-order and surface what is genuinely in the CV, and flag real gaps. "
    "Use UK spelling and no em-dashes. Return JSON only."
)


@dataclass
class Recommendation:
    fit_score: int = 0
    fit_reasoning: str = ""
    gaps: list[str] = field(default_factory=list)
    ats_keywords: list[str] = field(default_factory=list)
    sections: dict = field(default_factory=dict)
    cover_letter: str = ""

    def to_markdown(self) -> str:
        parts: list[str] = []
        if self.fit_reasoning:
            parts.append(f"**Fit {self.fit_score}/100** — {self.fit_reasoning}")
        if self.ats_keywords:
            parts.append("**ATS keywords to include:** " + ", ".join(self.ats_keywords))
        for key, label in (("summary", "Profile / summary"), ("skills", "Skills"),
                           ("experience", "Experience bullets"), ("projects", "Projects"),
                           ("education", "Education"), ("other", "Other ATS tips")):
            val = self.sections.get(key)
            if val:
                parts.append(f"**{label}**\n\n{val}")
        if self.gaps:
            parts.append("**Gaps to address**\n\n" + "\n".join(f"- {g}" for g in self.gaps))
        return "\n\n".join(parts)


def _to_rec(d: dict) -> Recommendation:
    sec = d.get("sections") if isinstance(d.get("sections"), dict) else {}
    return Recommendation(
        fit_score=int(d.get("fit_score", 0) or 0),
        fit_reasoning=str(d.get("fit_reasoning", "")),
        gaps=[str(x) for x in (d.get("gaps") or [])],
        ats_keywords=[str(x) for x in (d.get("ats_keywords") or [])],
        sections={k: str(v) for k, v in sec.items() if v},
        cover_letter=str(d.get("cover_letter", "")),
    )


def recommend(llm: LLM, base_cv: dict, job: dict, profile: dict | None = None) -> Recommendation:
    user = (
        "CANDIDATE CV (the ONLY source of truth - never invent beyond this):\n"
        f"{json.dumps(base_cv, ensure_ascii=False)}\n\n"
        + (f"CANDIDATE PREFERENCES:\n{json.dumps(profile)}\n\n" if profile else "")
        + "JOB TO TAILOR FOR:\n"
        f"Title: {job.get('title')}\nCompany: {job.get('company')}\nLocation: {job.get('location')}\n"
        f"Description:\n{(job.get('description') or '')[:3000]}\n\n"
        'Return ONLY JSON exactly: {"fit_score": <int 0-100>, "fit_reasoning": "1-2 sentences", '
        '"gaps": ["real gaps vs this JD"], "ats_keywords": ["exact skills/keywords from the JD to include"], '
        '"sections": {'
        '"summary": "how to rewrite the profile/summary for this job (2-4 sentences of concrete guidance)", '
        '"skills": "which skills to surface first and the exact keywords to add for the ATS", '
        '"experience": "which bullets to emphasise or rephrase and the ATS phrasing to use (reference the real CV)", '
        '"projects": "which projects to feature and why", '
        '"education": "any tweak or empty string", '
        '"other": "formatting/ATS tips e.g. match the exact job title, spell out acronyms, keyword density"}, '
        '"cover_letter": "a COMPLETE cover letter as plain text in this structure: '
        "'Dear Hiring Manager,' then para 1 (applying for <this role> + who I am: a Data Scientist with 3+ years at "
        "Infosys across retail, consumer goods and insurance, MSc Data Science at Coventry University with Distinction); "
        "para 2 (2-3 achievements FROM THE CV most relevant to this job, using the CV's real figures); "
        "para 3 (based in Birmingham, UK Graduate visa so able to work without sponsorship, welcome to discuss); "
        "'Thank you for considering my application.' then 'Yours sincerely,' then 'Manoj Ram Mopati'. "
        'Truthful to the CV, UK spelling, no em-dashes."}'
    )
    data = llm.complete_json(SYSTEM, user, provider=llm.tailor_provider, model=llm.tailor_model)
    return _to_rec(data)
