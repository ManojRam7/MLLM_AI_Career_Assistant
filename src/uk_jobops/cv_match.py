"""Pick the RIGHT ready-made CV for a job (no tailoring — Manoj maintains 9 role-specific CVs).

Given a job title + description, choose the single best-fit CV file from data/cvs/:
  AI Engineer · Analytics Engineer · Data Analyst · Senior Data Analyst ·
  Data Scientist (Azure / GCP / AWS) · Lead Data Scientist · Senior Data Scientist

Deterministic, zero-cost: category (via filtering.job_category) + seniority (from the title) +
cloud emphasis (counted in the JD) decide it. Azure is the default DS CV (his primary stack)."""
from __future__ import annotations

import re

from .filtering import job_category

# CV key -> file name in data/cvs/
CV_FILES = {
    "ai-engineer":         "AI Engineer.docx",
    "analytics-engineer":  "Analytics Engineer.docx",
    "data-analyst":        "Data Analyst.docx",
    "senior-data-analyst": "Senior Data Analyst.docx",
    "ds-azure":            "DataScientist-Azure.docx",
    "ds-gcp":              "DataScientist-GCP.docx",
    "ds-aws":              "DataScientist-AWS.docx",
    "ds-lead":             "DataScientist-Lead.docx",
    "ds-senior":           "DataScientist-Senior.docx",
}
CV_LABEL = {
    "ai-engineer": "AI Engineer", "analytics-engineer": "Analytics Engineer",
    "data-analyst": "Data Analyst", "senior-data-analyst": "Senior Data Analyst",
    "ds-azure": "Data Scientist (Azure)", "ds-gcp": "Data Scientist (GCP)",
    "ds-aws": "Data Scientist (AWS)", "ds-lead": "Lead Data Scientist",
    "ds-senior": "Senior Data Scientist",
}

_SENIOR = re.compile(r"\b(senior|sr|lead|principal|staff|head)\b", re.I)
_LEAD = re.compile(r"\b(lead|principal|head|staff)\b", re.I)
_GCP = re.compile(r"\b(gcp|google cloud|bigquery|big query|vertex ai|dataflow|looker|dataproc)\b", re.I)
_AWS = re.compile(r"\b(aws|amazon web services|sagemaker|redshift|glue|athena|\bec2\b|\bs3\b|lambda|emr)\b", re.I)
_AZURE = re.compile(r"\b(azure|databricks|data factory|synapse|azure ml|fabric|adf|data lake)\b", re.I)


def match_cv_key(title: str, description: str = "", category: str | None = None) -> str:
    """Return the CV key that best fits this job."""
    t = (title or "")
    blob = f"{t} {description or ''}"
    cat = category or job_category(t)
    senior = bool(_SENIOR.search(t))
    lead = bool(_LEAD.search(t))

    # Analytics Engineer has its own CV even though it sits in the data-analysis category
    if re.search(r"\banalytics engineer\b", t, re.I):
        return "analytics-engineer"
    if cat == "ai-engineer" or re.search(r"\b(ml engineer|machine learning engineer|ai engineer|mlops)\b", t, re.I):
        return "ai-engineer"
    if cat == "data-analysis":
        return "senior-data-analyst" if senior else "data-analyst"
    # data-science (default when unknown)
    if lead:
        return "ds-lead"
    if senior:
        return "ds-senior"
    # mid-level Data Scientist -> pick the cloud CV the JD emphasises (Azure is the default/primary)
    scores = {"ds-gcp": len(_GCP.findall(blob)), "ds-aws": len(_AWS.findall(blob)),
              "ds-azure": len(_AZURE.findall(blob))}
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "ds-azure"


def match_cv(title: str, description: str = "", category: str | None = None) -> tuple[str, str, str]:
    """Return (key, filename, label) of the best-fit CV for this job."""
    key = match_cv_key(title, description, category)
    return key, CV_FILES[key], CV_LABEL[key]
