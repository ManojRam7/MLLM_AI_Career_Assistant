"""Deterministic regression tests (no network/keys). Run with: pytest -q"""
from uk_jobops.dedupe import dedupe
from uk_jobops.filtering import apply_filters
from uk_jobops.llm.tailor import _to_tailored
from uk_jobops.llm.validator import validate
from uk_jobops.models import Job, TailoredCV
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
    assert len(targets) == 1          # Acme deduped across two sources
    assert len(rejected) == 2         # Senior + Marketing dropped


def test_coercion_handles_loose_model_json():
    t = _to_tailored({
        "skills": ["Python, SQL"],                                  # strings, not {label, items}
        "experience": [{"title": "X", "bullets": "did things"}],    # bullets as a string
        "projects": ["a RAG assistant"],                            # string
        "cover_letter": "Dear\n\nbody\n\nYours sincerely",          # one block
        "fit_score": "80",
    })
    assert all(isinstance(s, dict) and "label" in s for s in t.skills)
    assert isinstance(t.experience[0]["bullets"], list)
    assert t.projects[0]["text"].startswith("a RAG")
    assert len(t.cover_letter) == 3
    assert t.fit_score == 80


def test_validator_blocks_ai_traces():
    bad = TailoredCV(profile="A leverage-driven, seamless approach — really", jd_keywords=["python"])
    ok, issues = validate(bad)
    assert not ok and issues


def test_validator_passes_clean():
    good = TailoredCV(
        profile="Data Scientist with Python and SQL experience in the UK.",
        skills=[{"label": "Languages", "items": "Python, SQL"}],
        jd_keywords=["python", "sql"],
        cover_letter=["Dear", "p1", "p2", "p3", "Yours sincerely"],
    )
    ok, _ = validate(good)
    assert ok
