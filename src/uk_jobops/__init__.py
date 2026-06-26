"""uk-jobops: automated UK data-science job discovery, CV tailoring and tracking.

Pipeline: discover (Reed/Adzuna/ATS) -> normalise + dedupe + seniority filter
-> store (Supabase Postgres) -> LLM fit score -> tailor CV + cover letter
(rulebook + deterministic validator) -> tracker (with manual custom-job add)
-> digest. Runs on GitHub Actions every 6 hours.
"""
__version__ = "0.1.0"
