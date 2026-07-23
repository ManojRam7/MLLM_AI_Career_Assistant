"""Per-job HONEST ATS audit + tailoring recommendations + a cover letter (TEXT, no documents).

One LLM call returns a strict, recruiter-grade audit of the candidate's CV against ONE job:
a strict current-match score + realistic after-tailoring potential, a requirement-by-requirement
match table (Strong/Partial/Gap), the biggest hard gaps named explicitly, keywords split into three
honesty tiers (evidenced / only-with-an-example / never-claim), section-by-section tailoring guidance,
and a truthful cover letter. It NEVER invents experience and NEVER changes the candidate's real job
titles. Rendered to markdown for the dashboard. Uses the quality tailor provider + profile rubric."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .client import LLM

SYSTEM = (
    "You are a STRICT, honest UK recruiter and ATS-optimisation specialist auditing ONE candidate's real "
    "CV against ONE job. Behave like a sceptical hiring manager, not a cheerleader.\n"
    "PRINCIPLES:\n"
    "1. Judge DIRECT, EVIDENCED match to the job's hard requirements - do NOT confuse transferable skill "
    "with a direct match. A role built 'mostly on BigQuery' is a real gap for someone with no BigQuery.\n"
    "2. NEVER invent or imply experience the candidate does not have. If the JD's primary tool is absent "
    "from the CV (e.g. BigQuery, Looker, dbt), say so plainly and put it in the 'never-claim' tier.\n"
    "3. Adjacent tools are PARTIAL only (Azure vs BigQuery, Looker Studio vs Looker, Power BI vs Tableau).\n"
    "4. NEVER tell the candidate to change their real employment titles; a target title at the top of the "
    "CV is fine, but the Infosys roles keep their actual titles.\n"
    "5. Do NOT advise 'keyword density' or stuffing - keywords must appear naturally and be backed by "
    "evidence.\n"
    "6. Use ONLY the figures in the CV. Use UK spelling and no em-dashes.\n"
    "Return JSON only."
)


@dataclass
class Recommendation:
    strict_score: int = 0
    potential_score: int = 0
    verdict: str = ""
    requirements: list = field(default_factory=list)     # [{requirement, evidence, match}]
    gaps: list[str] = field(default_factory=list)         # biggest hard gaps (named)
    keywords_use: list[str] = field(default_factory=list)
    keywords_with_example: list[str] = field(default_factory=list)
    keywords_never: list[str] = field(default_factory=list)
    sections: dict = field(default_factory=dict)
    cover_letter: str = ""

    # kept for backwards-compatibility (pipeline / older callers)
    @property
    def fit_score(self) -> int:
        return self.strict_score

    @property
    def fit_reasoning(self) -> str:
        return self.verdict

    def to_markdown(self) -> str:
        p: list[str] = []
        if self.strict_score or self.potential_score:
            head = f"**Strict match {self.strict_score}/100**"
            if self.potential_score:
                head += f"  ·  ~{self.potential_score}/100 after honest tailoring"
            p.append(head + (f"\n\n{self.verdict}" if self.verdict else ""))
        elif self.verdict:
            p.append(self.verdict)

        if self.gaps:
            p.append("**Biggest gaps (address or acknowledge)**\n\n"
                     + "\n".join(f"- {g}" for g in self.gaps))

        if self.requirements:
            rows = ["| Requirement | Your evidence | Match |", "| --- | --- | --- |"]
            for r in self.requirements:
                if not isinstance(r, dict):
                    continue
                req = str(r.get("requirement", "")).replace("|", "/")
                ev = str(r.get("evidence", "")).replace("|", "/")
                m = str(r.get("match", "")).replace("|", "/")
                rows.append(f"| {req} | {ev} | {m} |")
            p.append("**Requirement-by-requirement match**\n\n" + "\n".join(rows))

        if self.keywords_use:
            p.append("**Keywords - use prominently (evidenced)**\n\n" + ", ".join(self.keywords_use))
        if self.keywords_with_example:
            p.append("**Keywords - use ONLY with a real example**\n\n" + ", ".join(self.keywords_with_example))
        if self.keywords_never:
            p.append("**Do NOT claim without real experience**\n\n" + ", ".join(self.keywords_never))

        for key, label in (("positioning", "CV positioning / target heading"), ("summary", "Profile / summary"),
                           ("skills", "Skills"), ("experience", "Experience bullets"),
                           ("projects", "Projects"), ("education", "Education")):
            val = self.sections.get(key)
            if val:
                p.append(f"**{label}**\n\n{val}")
        return "\n\n".join(p)


def _to_rec(d: dict) -> Recommendation:
    sec = d.get("sections") if isinstance(d.get("sections"), dict) else {}
    kw = d.get("keywords") if isinstance(d.get("keywords"), dict) else {}
    return Recommendation(
        strict_score=int(d.get("strict_score", d.get("fit_score", 0)) or 0),
        potential_score=int(d.get("potential_score", 0) or 0),
        verdict=str(d.get("verdict", d.get("fit_reasoning", ""))),
        requirements=[r for r in (d.get("requirements") or []) if isinstance(r, dict)],
        gaps=[str(x) for x in (d.get("gaps") or d.get("biggest_gaps") or [])],
        keywords_use=[str(x) for x in (kw.get("use") or d.get("keywords_use") or [])],
        keywords_with_example=[str(x) for x in (kw.get("with_example") or d.get("keywords_with_example") or [])],
        keywords_never=[str(x) for x in (kw.get("never") or d.get("keywords_never") or [])],
        sections={k: str(v) for k, v in sec.items() if v},
        cover_letter=str(d.get("cover_letter", "")),
    )


def recommend(llm: LLM, base_cv: dict, job: dict, profile: dict | None = None) -> Recommendation:
    user = (
        "CANDIDATE CV (the ONLY source of truth - never invent beyond this, never change real job titles):\n"
        f"{json.dumps(base_cv, ensure_ascii=False)}\n\n"
        + (f"CANDIDATE PREFERENCES / RUBRIC:\n{json.dumps(profile)}\n\n" if profile else "")
        + "JOB TO AUDIT AND TAILOR FOR:\n"
        f"Title: {job.get('title')}\nCompany: {job.get('company')}\nLocation: {job.get('location')}\n"
        f"Description:\n{(job.get('description') or '')[:3500]}\n\n"
        "Audit the CV against THIS job like a strict recruiter, then return ONLY this JSON:\n"
        "{"
        '"strict_score": <int 0-100, honest CURRENT match to the hard requirements>, '
        '"potential_score": <int 0-100, realistic ceiling after HONEST tailoring, no invented skills>, '
        '"verdict": "2-3 sentences, gap-first: strongest overlaps then the biggest missing hard requirements", '
        '"requirements": [{"requirement": "a specific JD requirement", "evidence": "the candidate\'s real evidence or \'none\'", "match": "Strong|Partial|Gap"}], '
        '"gaps": ["the hard requirements the candidate genuinely lacks, named (e.g. BigQuery, Looker, dbt)"], '
        '"keywords": {'
        '"use": ["JD keywords the CV already evidences - use prominently"], '
        '"with_example": ["JD keywords to use ONLY if a concrete example supports them"], '
        '"never": ["JD keywords the candidate must NOT claim (no real experience)"]}, '
        '"sections": {'
        '"positioning": "a target heading + how to position the CV (keep REAL job titles)", '
        '"summary": "how to rewrite the profile for this job, honestly (2-4 sentences)", '
        '"skills": "which real skills to surface first and the exact evidenced keywords to add", '
        '"experience": "which real bullets to emphasise/rephrase and the ATS phrasing (reference the actual CV, keep titles)", '
        '"projects": "which projects to feature and why (only if they genuinely support the claim)", '
        '"education": "any honest tweak or empty string"}, '
        '"cover_letter": "a COMPLETE truthful cover letter as plain text: '
        "'Dear Hiring Manager,' then para 1 (applying for <this role>; who I am, adapted to the role: 3+ years at "
        "Infosys across retail, consumer goods and insurance, MSc Data Science at Coventry University with Distinction); "
        "para 2 (2-3 achievements FROM THE CV most relevant to this job, using the CV's REAL figures); "
        "para 3 (if the JD's primary tool is not in the CV, honestly note the transferable Azure/SQL foundation instead "
        "of claiming it; based in Birmingham, UK Graduate visa, able to work without sponsorship); "
        "'Thank you for considering my application.' then 'Yours sincerely,' then 'Manoj Ram Mopati'. "
        'Truthful to the CV, UK spelling, no em-dashes."}'
    )
    data = llm.complete_json(SYSTEM, user, provider=llm.tailor_provider, model=llm.tailor_model)
    return _to_rec(data)
