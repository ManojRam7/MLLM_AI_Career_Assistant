"""Deterministic regression tests (no network/keys). Run with: pytest -q"""
from uk_jobops.dedupe import dedupe
from uk_jobops.filtering import apply_filters, is_agency, job_category
from uk_jobops.models import Job
from uk_jobops.normalize import normalize


def test_filter_and_dedupe():
    jobs = [
        Job(title="Data Scientist", company="Acme", location="London"),
        Job(title="Data Scientist", company="Acme", location="London, UK"),
        Job(title="Senior Data Scientist", company="Beta", location="Leeds"),
        Job(title="Marketing Manager", company="Gamma", location="Remote"),
    ]
    normalize(jobs)
    targets, rejected = apply_filters(jobs, ["data scientist"], ["senior", "lead", "manager"])
    targets = dedupe(targets)
    assert len(targets) == 1          # Acme deduped across two rows
    assert len(rejected) == 2         # Senior + Marketing dropped


def test_category_classifier():
    # three categories now: data-science, ai-engineer, data-analysis
    assert job_category("Data Scientist") == "data-science"
    assert job_category("Applied Scientist") == "data-science"
    assert job_category("Machine Learning Engineer") == "ai-engineer"   # AI/ML build role
    assert job_category("AI Engineer") == "ai-engineer"
    assert job_category("LLM Engineer") == "ai-engineer"
    assert job_category("Data Analyst") == "data-analysis"
    assert job_category("Business Intelligence Analyst") == "data-analysis"
    assert job_category("Data Science Analyst") == "data-science"   # DS signal beats analyst
    assert job_category("Machine Learning Data Scientist") == "ai-engineer"  # data+AI mixed -> AI


def test_agency_and_spam_filtering():
    jobs = [
        Job(title="Data Analyst", company="Barclays", description="Join Barclays"),
        Job(title="Data Analyst", company="Acme", description="Our client is a leading bank"),
        Job(title="Data Analyst Apprentice", company="QA", description="Learn on the job"),
    ]
    targets, rejected = apply_filters(jobs, ["data analyst"], ["senior"], exclude_recruiters=True)
    kept = {j.company for j in targets}
    assert kept == {"Barclays"}                    # agency + apprentice dropped
    assert is_agency("Acme", "Our client is a leading bank")
    assert not is_agency("Barclays", "Join Barclays")
