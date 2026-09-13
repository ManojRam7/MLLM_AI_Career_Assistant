"""Supabase / Postgres storage + tracker + run history.
Connect DBeaver (or the dashboard) to the same SUPABASE_DB_URL."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .config import ConfigError
from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    dedupe_key   TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    company      TEXT,
    location     TEXT,
    url          TEXT,
    description  TEXT,
    posted_date  TEXT,
    salary       TEXT,
    remote       BOOLEAN DEFAULT FALSE,
    source       TEXT,
    source_query TEXT,
    seniority    TEXT,
    is_target    BOOLEAN DEFAULT TRUE,
    fit_score    INTEGER DEFAULT 0,
    fit_reasoning TEXT,
    ghost_flag   BOOLEAN DEFAULT FALSE,
    notified     BOOLEAN DEFAULT FALSE,
    status       TEXT DEFAULT 'new',
    is_custom    BOOLEAN DEFAULT FALSE,
    tracked      BOOLEAN DEFAULT FALSE,
    in_bucket    BOOLEAN DEFAULT FALSE,
    bucket_tier  TEXT DEFAULT '',
    notes        TEXT DEFAULT '',
    locations    TEXT DEFAULT '',
    applied_at   TEXT DEFAULT '',
    cv_path      TEXT,
    cover_path   TEXT,
    cv_blob      BYTEA,
    cover_blob   BYTEA,
    gaps         JSONB DEFAULT '[]',
    first_seen_at TEXT,
    last_seen_at  TEXT
);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS in_bucket BOOLEAN DEFAULT FALSE;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS applied_at TEXT DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cv_blob BYTEA;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cover_blob BYTEA;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS locations TEXT DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS bucket_tier TEXT DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS notified BOOLEAN DEFAULT FALSE;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tracked BOOLEAN DEFAULT FALSE;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS category TEXT DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS sector TEXT DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS recommendations TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cover_text TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cv_keywords TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS country TEXT DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS visa_sponsorship TEXT DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS geo_score INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS matched_cv TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_fit_idx ON jobs(fit_score DESC);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id          BIGSERIAL PRIMARY KEY,
    run_at      TEXT,
    mode        TEXT,
    discovered  INTEGER, targets INTEGER, rejected INTEGER,
    scored      INTEGER, tailored INTEGER, stored_new INTEGER,
    llm_note    TEXT,
    summary_json JSONB
);
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS summary_json JSONB;

CREATE TABLE IF NOT EXISTS apply_log (
    id          BIGSERIAL PRIMARY KEY,
    dedupe_key  TEXT,
    company     TEXT,
    role_title  TEXT,
    country     TEXT DEFAULT '',
    url         TEXT,
    cv          TEXT,
    status      TEXT DEFAULT 'submitted',
    screenshot  BYTEA,
    applied_at  TIMESTAMPTZ DEFAULT now()
);
-- URLs the user queues from the Streamlit control panel (phone/laptop) for the LOCAL runner to apply to
CREATE TABLE IF NOT EXISTS apply_requests (
    id           BIGSERIAL PRIMARY KEY,
    url          TEXT NOT NULL,
    title        TEXT DEFAULT '',
    company      TEXT DEFAULT '',
    source       TEXT DEFAULT 'pasted',   -- 'queue' (from scored jobs) | 'pasted' (manual URL)
    auto_submit  BOOLEAN DEFAULT FALSE,
    status       TEXT DEFAULT 'queued',   -- queued | processing | done | needs_submit | skipped | error
    note         TEXT DEFAULT '',
    requested_at TIMESTAMPTZ DEFAULT now(),
    processed_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS apply_requests_url_open
    ON apply_requests (url) WHERE status IN ('queued','processing');
-- Step-by-step screenshots of an application being filled, so a headless CLOUD run is just as
-- watchable as a local one (rendered as a filmstrip in the app).
CREATE TABLE IF NOT EXISTS apply_steps (
    id         BIGSERIAL PRIMARY KEY,
    url        TEXT,
    company    TEXT DEFAULT '',
    role_title TEXT DEFAULT '',
    run_id     TEXT DEFAULT '',
    step_no    INT DEFAULT 0,
    label      TEXT DEFAULT '',
    note       TEXT DEFAULT '',
    shot       BYTEA,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS apply_steps_run ON apply_steps (run_id, step_no);
"""

UPSERT = """
INSERT INTO jobs (dedupe_key,title,company,location,locations,url,description,posted_date,salary,remote,
                  source,source_query,seniority,category,sector,is_target,in_bucket,bucket_tier,first_seen_at,last_seen_at,is_custom,status)
VALUES (%(dedupe_key)s,%(title)s,%(company)s,%(location)s,%(locations)s,%(url)s,%(description)s,%(posted_date)s,
        %(salary)s,%(remote)s,%(source)s,%(source_query)s,%(seniority)s,%(category)s,%(sector)s,%(is_target)s,%(in_bucket)s,%(bucket_tier)s,
        %(first_seen_at)s,%(last_seen_at)s,%(is_custom)s,%(status)s)
ON CONFLICT (dedupe_key) DO UPDATE SET
    last_seen_at = EXCLUDED.last_seen_at,
    in_bucket = jobs.in_bucket OR EXCLUDED.in_bucket,
    category = COALESCE(NULLIF(EXCLUDED.category,''), jobs.category),
    sector = COALESCE(NULLIF(EXCLUDED.sector,''), jobs.sector),
    bucket_tier = COALESCE(NULLIF(EXCLUDED.bucket_tier,''), jobs.bucket_tier),
    locations = CASE WHEN length(COALESCE(EXCLUDED.locations,'')) > length(COALESCE(jobs.locations,''))
                     THEN EXCLUDED.locations ELSE jobs.locations END,
    url = COALESCE(NULLIF(EXCLUDED.url,''), jobs.url),
    description = COALESCE(NULLIF(EXCLUDED.description,''), jobs.description)
RETURNING (xmax = 0) AS inserted;
"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, db_url: str):
        u = (db_url or "").strip()
        if not (u.startswith("postgresql://") or u.startswith("postgres://")):
            raise ConfigError(
                "SUPABASE_DB_URL is not a Postgres connection string. It must start with "
                "'postgresql://'. Copy it from Supabase > Project Settings > Database > "
                "Connection string > URI (Transaction pooler, ends ':6543/postgres') and put your "
                "database password in it. A Supabase API key (anon/service_role) will not work.")
        import psycopg

        # prepare_threshold=None disables psycopg3 auto-prepared-statements, which BREAK on Supabase's
        # transaction pooler (PgBouncer) with 'prepared statement "_pg3_0" already exists'. Required.
        self.conn = psycopg.connect(u, autocommit=True, prepare_threshold=None)

    def init_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(SCHEMA)

    # ------------------------------------------------------------------ writes
    def upsert_jobs(self, jobs: list[Job]) -> tuple[int, int]:
        new = dup = 0
        with self.conn.cursor() as cur:
            for j in jobs:
                row = j.to_db(); row["last_seen_at"] = _now()
                cur.execute(UPSERT, row)
                inserted = cur.fetchone()[0]
                new += int(inserted); dup += int(not inserted)
        return new, dup

    def add_custom_job(self, *, title: str, company: str, url: str, location: str = "",
                       description: str = "", status: str = "shortlisted") -> str:
        j = Job(title=title, company=company, url=url, location=location, description=description,
                source="Manual", is_custom=True, status=status).finalize()
        row = j.to_db(); row["last_seen_at"] = _now()
        with self.conn.cursor() as cur:
            cur.execute(UPSERT, row)
        self.update(j.dedupe_key, tracked=True)   # manual jobs go straight into the tracker
        return j.dedupe_key

    def set_tracked(self, keys: list[str], tracked: bool = True) -> None:
        if not keys:
            return
        with self.conn.cursor() as cur:
            cur.execute("UPDATE jobs SET tracked=%s WHERE dedupe_key = ANY(%s)", (tracked, list(keys)))

    def delete_jobs(self, keys: list[str]) -> None:
        if not keys:
            return
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM jobs WHERE dedupe_key = ANY(%s)", (list(keys),))

    def update(self, dedupe_key: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = %s" for k in fields)
        vals = [json.dumps(v) if k == "gaps" else v for k, v in fields.items()]
        with self.conn.cursor() as cur:
            cur.execute(f"UPDATE jobs SET {sets} WHERE dedupe_key = %s", (*vals, dedupe_key))

    def mark_notified(self, keys: list[str]) -> None:
        if not keys:
            return
        with self.conn.cursor() as cur:
            cur.execute("UPDATE jobs SET notified=TRUE WHERE dedupe_key = ANY(%s)", (list(keys),))

    def jobs_to_notify(self, min_fit: int = 75, limit: int = 10, max_age_days: int = 2) -> list[dict[str, Any]]:
        # Only FRESH (last few days) high-fit jobs, newest first - so stale backlog never floods you.
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).replace(microsecond=0).isoformat()
        return self._rows(
            "SELECT dedupe_key,title,company,location,locations,fit_score,fit_reasoning,url,in_bucket,"
            "bucket_tier,category,sector,cv_keywords,first_seen_at,posted_date "
            "FROM jobs WHERE notified=FALSE AND is_target=TRUE AND fit_score >= %s AND first_seen_at >= %s "
            "ORDER BY first_seen_at DESC, fit_score DESC LIMIT %s", (min_fit, cutoff, limit))

    def set_status(self, dedupe_key: str, status: str, notes: str | None = None) -> None:
        fields: dict[str, Any] = {"status": status}
        if status == "applied":
            fields["applied_at"] = _now()[:10]
        if notes is not None:
            fields["notes"] = notes
        self.update(dedupe_key, **fields)

    def log_run(self, summary: dict[str, Any]) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pipeline_runs (run_at,mode,discovered,targets,rejected,scored,tailored,"
                "stored_new,llm_note,summary_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (_now(), summary.get("mode"), summary.get("discovered"), summary.get("targets"),
                 summary.get("rejected"), summary.get("scored"), summary.get("tailored"),
                 summary.get("stored_new"), summary.get("llm_note", ""), json.dumps(summary, default=str)))

    def purge_excluded(self, exclude_title: list[str], exclude_company: list[str],
                       exclude_recruiters: bool = True) -> int:
        """Delete already-stored rows that now match the exclude rules or are recruitment
        agencies (junk/recruiters stored before the filters existed). Skips manually-added
        jobs. Idempotent - safe every run."""
        clauses: list[str] = []
        params: list[str] = []
        for term in (exclude_title or []):
            clauses.append("title ~* %s")
            params.append(r"\y" + re.escape(term) + r"\y")  # whole-word, mirrors filtering.py
        for term in (exclude_company or []):
            clauses.append("company ILIKE %s")
            params.append(f"%{term}%")
        if exclude_recruiters:
            clauses.append(r"company ~* '\y(recruit\w*|staffing|resourc\w*|rpo|headhunt\w*)\y'")
        if not clauses:
            return 0
        sql = "DELETE FROM jobs WHERE is_custom = FALSE AND (" + " OR ".join(clauses) + ")"
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def purge_sources(self, patterns: list[str]) -> int:
        """Delete rows from retired/legacy sources (e.g. old 'Google (Apify)' rows). Skips manual jobs."""
        patterns = [p for p in (patterns or []) if p]
        if not patterns:
            return 0
        clause = " OR ".join(["source ILIKE %s"] * len(patterns))
        with self.conn.cursor() as cur:
            cur.execute(f"DELETE FROM jobs WHERE is_custom = FALSE AND ({clause})",
                        [f"%{p}%" for p in patterns])
            return cur.rowcount

    def reset_serp(self) -> int:
        """One-time cleanup: delete every non-tracked, non-manual Bright Data (SERP) job so the
        now-clean pipeline can repopulate from scratch (old rows have stale/fake-UK data that the
        pattern purges can't detect). Tracked and manually-added jobs are kept."""
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM jobs WHERE is_custom = FALSE AND tracked = FALSE "
                        "AND source ILIKE %s", ("%Bright Data%",))
            return cur.rowcount

    def wipe_all(self) -> int:
        """DESTRUCTIVE full reset: delete EVERY job (including tracked + manual) and all run history,
        for a clean rebuild from scratch. Triggered by run_pipeline --wipe-all (workflow 'wipe' input)."""
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM jobs")
            n = cur.rowcount
            cur.execute("DELETE FROM pipeline_runs")
        return n

    def purge_spam(self, keep_nonuk: bool = False) -> int:
        """Delete already-stored expired / removed / stale-aggregator postings. When keep_nonuk is
        False (UK-only mode) it ALSO deletes non-UK rows; when True (global search) non-UK rows are
        KEPT (ranked by geo_score instead). Skips manual and tracked jobs. Idempotent - runs each pass."""
        expired = (r"(no longer (accepting|available)|has been (filled|removed)|was removed|position "
                   r"(has been |is )?filled|applications? (are |have )?closed|\yexpired\y|"
                   r"this (job|vacancy|position) (has|was) (been )?(removed|filled|closed|expired))")
        nonuk = (r"\y(india|mumbai|bangalore|bengaluru|hyderabad|pune|gurgaon|gurugram|chennai|noida|"
                 r"united states|\yusa\y|new york|san francisco|california|texas|boston|chicago|dallas|"
                 r"miami|atlanta|alpharetta|boise|seattle|canada|toronto|vancouver|montreal|"
                 r"france|paris|montrouge|germany|berlin|munich|spain|madrid|barcelona|portugal|lisbon|"
                 r"porto|netherlands|amsterdam|dubai|\yuae\y|qatar|saudi|poland|krak|warsaw|romania|"
                 r"bucharest|singapore|hong kong|tokyo|japan|australia|sydney|melbourne|brisbane|"
                 r"ireland|dublin|brazil|mexico)\y")
        uk = (r"\y(united kingdom|england|scotland|wales|northern ireland|\yuk\y|london|manchester|"
              r"birmingham|leeds|glasgow|edinburgh|bristol|cardiff|liverpool|sheffield|newcastle|"
              r"nottingham|coventry|reading|oxford|cambridge|belfast|leicester|aberdeen|remote uk)\y")
        # untrusted aggregator / stale-listing hosts (kept jobs on the company's OWN domain or a
        # trusted board are untouched)
        agg = (r"(builtin|bebee|expertini|welcometothejungle|otta\.|datasciencejobs|stacksignal|"
               r"efinancialcareers|canarywharfian|bulldogjob|alooba|glassdoor|artificialintelligencejobs|"
               r"harnham|jobrapido|neuvoo|talent\.com|jooble|whatjobs|opendatascience|careerjet|jobsora|"
               r"hackajob)")
        nonuk_clause = ("""
            OR (
                (title || ' ' || coalesce(description,'') || ' ' || coalesce(location,'')) ~* %s
                AND (title || ' ' || coalesce(description,'') || ' ' || coalesce(location,'')) !~* %s
            )""" if not keep_nonuk else "")
        sql = f"""
        DELETE FROM jobs WHERE is_custom = FALSE AND tracked = FALSE AND (
            url ~* %s
            OR (title || ' ' || coalesce(description,'')) ~* %s
            {nonuk_clause})"""
        params = (agg, expired) + ((nonuk, uk) if not keep_nonuk else ())
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def purge_expired(self, max_days: int = 60) -> int:
        """Delete stored jobs whose ISO posted date is older than max_days (expired postings).
        Only touches rows with a real YYYY-MM-DD posted_date; keeps manual/tracked jobs."""
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM jobs WHERE is_custom = FALSE AND tracked = FALSE "
                "AND posted_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' "
                "AND to_date(substring(posted_date, 1, 10), 'YYYY-MM-DD') < current_date - %s",
                (max_days,))
            return cur.rowcount

    def prune_broad_market(self, min_fit: int = 75) -> int:
        """Raise the bar on the broad market: delete non-bucket ('— other —') jobs that have been
        SCORED below min_fit, so lesser-known minors/startups don't clutter the feed. Always keeps
        bucket-list top companies, tracked/manual jobs, and not-yet-scored jobs (fit_score = 0)."""
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM jobs WHERE is_custom = FALSE AND tracked = FALSE "
                        "AND in_bucket = FALSE AND fit_score > 0 AND fit_score < %s", (min_fit,))
            return cur.rowcount

    def collapse_duplicates(self) -> int:
        """Keep ONE row per (title, company, first-city). Adzuna returns the same job under
        several tracking URLs, so URL-identity leaves cross-run duplicates - this cleans them,
        keeping the most useful copy (tracked > progressed > has recommendations > highest fit >
        earliest seen). Skips manual jobs."""
        sql = """
        WITH ranked AS (
          SELECT dedupe_key, row_number() OVER (
            PARTITION BY lower(btrim(title)), lower(btrim(company)),
                         lower(split_part(coalesce(location,''), ',', 1))
            ORDER BY tracked DESC,
                     (status IN ('applied','interview','offer','assessment_cleared','cleared')) DESC,
                     (recommendations IS NOT NULL) DESC,
                     fit_score DESC, first_seen_at ASC
          ) AS rn
          FROM jobs WHERE is_custom = FALSE
        )
        DELETE FROM jobs WHERE dedupe_key IN (SELECT dedupe_key FROM ranked WHERE rn > 1)
        """
        with self.conn.cursor() as cur:
            cur.execute(sql)
            return cur.rowcount

    # ------------------------------------------------------------------- reads
    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def jobs_needing_enrichment(self, limit: int = 800) -> list[dict[str, Any]]:
        """Every job still missing CV keywords OR geo tagging (country/visa). Deterministic backfill,
        zero LLM cost, runs each pass so ALL jobs — new and historic — get keywords + country + visa."""
        return self._rows(
            "SELECT dedupe_key,title,company,location,locations,description FROM jobs "
            "WHERE cv_keywords IS NULL OR cv_keywords = '' OR country IS NULL OR country = '' "
            "OR matched_cv IS NULL OR matched_cv = '' "
            "ORDER BY (bucket_tier='top100') DESC, in_bucket DESC, fit_score DESC LIMIT %s", (limit,))

    def jobs_needing_score(self, limit: int = 40) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT dedupe_key,title,company,location,description,in_bucket,bucket_tier FROM jobs "
            "WHERE status='new' AND is_target=TRUE AND fit_score=0 "
            "ORDER BY (bucket_tier='top100') DESC, in_bucket DESC LIMIT %s", (limit,))

    def jobs_to_recommend(self, threshold: int, limit: int = 8) -> list[dict[str, Any]]:
        # generate ATS recommendations + cover letter once per job (recommendations IS NULL).
        return self._rows(
            "SELECT dedupe_key,title,company,location,description FROM jobs "
            "WHERE recommendations IS NULL AND is_target=TRUE "
            "AND (fit_score >= %s OR is_custom=TRUE) "
            "ORDER BY (bucket_tier='top100') DESC, in_bucket DESC, fit_score DESC LIMIT %s", (threshold, limit))

    def digest(self, min_fit: int = 70, limit: int = 20) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT title,company,location,url,fit_score FROM jobs WHERE fit_score >= %s "
            "ORDER BY first_seen_at DESC LIMIT %s", (min_fit, limit))

    def all_jobs(self, limit: int = 2000) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT dedupe_key,title,company,location,locations,source,in_bucket,bucket_tier,category,sector,fit_score,seniority,status,"
            "tracked,is_custom,notes,applied_at,url,cv_path,cover_path,fit_reasoning,ghost_flag,cv_keywords,"
            "country,visa_sponsorship,geo_score,matched_cv,posted_date,first_seen_at "
            "FROM jobs ORDER BY (bucket_tier='top100') DESC, in_bucket DESC, geo_score DESC, fit_score DESC, first_seen_at DESC LIMIT %s",
            (limit,))

    def recommendations_list(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT dedupe_key,title,company,category,location,fit_score,in_bucket,bucket_tier,"
            "fit_reasoning,url,recommendations,cover_text,cv_keywords "
            "FROM jobs WHERE recommendations IS NOT NULL OR cv_keywords IS NOT NULL "
            "ORDER BY (bucket_tier='top100') DESC, in_bucket DESC, fit_score DESC LIMIT %s", (limit,))

    # known ATS / direct-employer hosts (auto-apply targets — NOT Indeed/LinkedIn aggregators)
    _ATS_URL = (r"(greenhouse\.io|lever\.co|ashbyhq\.com|myworkdayjobs\.com|smartrecruiters\.com|"
                r"workable\.com|recruitee\.com|eightfold\.ai|personio\.|teamtailor\.com|"
                r"breezy\.hr|bamboohr\.com|icims\.com|higher\.gs\.com|wd\d+\.myworkdayjobs)")

    _APPLIED_STATUSES = "('applied','interview','offer','rejected','assessment','assessment_cleared')"

    def apply_queue(self, min_score: int = 95, limit: int = 100,
                    include_applied: bool = False) -> list[dict[str, Any]]:
        """Jobs eligible for AUTO-APPLY: score >= min_score, on a DIRECT-EMPLOYER ATS (Greenhouse/
        Lever/Ashby/Workday/SmartRecruiters/…), NOT Indeed/LinkedIn, and (unless include_applied)
        not already applied. Includes the matched CV so the apply agent knows which file to attach.
        include_applied=True is for LOCAL RE-TESTING — it re-surfaces already-applied roles."""
        status_gate = "" if include_applied else f"AND status NOT IN {self._APPLIED_STATUSES} "
        return self._rows(
            "SELECT dedupe_key,title,company,location,locations,url,fit_score,matched_cv,category,"
            "sector,country,visa_sponsorship,description,status "
            "FROM jobs WHERE is_target=TRUE AND fit_score >= %s "
            f"{status_gate}"
            "AND url ~* %s AND url !~* 'linkedin\\.com|indeed\\.' "
            "ORDER BY (bucket_tier='top100') DESC, fit_score DESC LIMIT %s",
            (min_score, self._ATS_URL, limit))

    def apply_stats(self, thr: int = 85) -> tuple[dict[str, int], list[dict[str, Any]]]:
        """Explain the apply queue at a given threshold: fit bands, direct-ATS counts, AND why
        eligible-looking jobs don't queue — how many high-fit jobs are stuck on LinkedIn/Indeed
        aggregator URLs (can't auto-fill), already applied, or not a target role. Plus the top
        direct-ATS roles by fit. Single source of truth for the empty-queue message."""
        ats = self._ATS_URL
        nagg = "url !~* 'linkedin\\.com|indeed\\.'"          # a real employer ATS url (fillable)
        agg = "url ~* 'linkedin\\.com|indeed\\.'"            # an aggregator url (NOT fillable)
        applied = f"status IN {self._APPLIED_STATUSES}"

        def _n(sql: str, params: tuple = ()) -> int:
            with self.conn.cursor() as c:
                c.execute(sql, params)
                return int(c.fetchone()[0])
        s = {
            "total": _n("SELECT count(*) FROM jobs WHERE fit_score>0"),
            "ge95": _n("SELECT count(*) FROM jobs WHERE fit_score>=95"),
            "ge90": _n("SELECT count(*) FROM jobs WHERE fit_score>=90"),
            "ge85": _n("SELECT count(*) FROM jobs WHERE fit_score>=85"),
            "ge80": _n("SELECT count(*) FROM jobs WHERE fit_score>=80"),
            "ats_total": _n(f"SELECT count(*) FROM jobs WHERE url ~* %s AND {nagg}", (ats,)),
            "ats85": _n(f"SELECT count(*) FROM jobs WHERE fit_score>=85 AND url ~* %s AND {nagg}", (ats,)),
            "ats90": _n(f"SELECT count(*) FROM jobs WHERE fit_score>=90 AND url ~* %s AND {nagg}", (ats,)),
            # --- at the ACTUAL threshold: exactly why the queue is (not) empty ---
            "thr": thr,
            "queue": _n(f"SELECT count(*) FROM jobs WHERE is_target=TRUE AND fit_score>=%s AND url ~* %s "
                        f"AND {nagg} AND status NOT IN {self._APPLIED_STATUSES}", (thr, ats)),
            "ats_thr": _n(f"SELECT count(*) FROM jobs WHERE fit_score>=%s AND url ~* %s AND {nagg}", (thr, ats)),
            "applied_thr": _n(f"SELECT count(*) FROM jobs WHERE fit_score>=%s AND url ~* %s AND {nagg} "
                              f"AND {applied}", (thr, ats)),
            "nontarget_thr": _n(f"SELECT count(*) FROM jobs WHERE fit_score>=%s AND url ~* %s AND {nagg} "
                                f"AND is_target=FALSE", (thr, ats)),
            "agg_thr": _n(f"SELECT count(*) FROM jobs WHERE fit_score>=%s AND {agg}", (thr,)),
        }
        top = self._rows(
            f"SELECT title,company,url,fit_score,matched_cv,status FROM jobs WHERE url ~* %s AND {nagg} "
            "AND fit_score>0 ORDER BY fit_score DESC LIMIT 12", (ats,))
        return s, top

    def log_apply(self, *, dedupe_key: str = "", company: str = "", role_title: str = "", country: str = "",
                  url: str = "", cv: str = "", status: str = "submitted",
                  screenshot: bytes | None = None) -> None:
        """Record an auto-apply attempt (with an optional confirmation screenshot) for the activity log."""
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO apply_log (dedupe_key,company,role_title,country,url,cv,status,screenshot) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (dedupe_key, company, role_title, country, url, cv, status, screenshot))

    def apply_log_rows(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT id,dedupe_key,company,role_title,country,url,cv,status,applied_at,"
            "(screenshot IS NOT NULL) AS has_shot FROM apply_log ORDER BY id DESC LIMIT %s", (limit,))

    def apply_screenshot(self, log_id: int) -> bytes | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT screenshot FROM apply_log WHERE id=%s", (log_id,))
            row = cur.fetchone()
            return bytes(row[0]) if row and row[0] is not None else None

    # ------------------------------------------------------------------ apply step screenshots
    def log_apply_step(self, *, url: str, company: str = "", role_title: str = "", run_id: str = "",
                       step_no: int = 0, label: str = "", note: str = "",
                       shot: bytes | None = None) -> None:
        """Save ONE screenshot of a stage of the fill (so a cloud run can be replayed visually)."""
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO apply_steps (url,company,role_title,run_id,step_no,label,note,shot) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (url, company, role_title, run_id, step_no, label, note[:300], shot))

    def apply_step_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        """Most recent applications that have a screenshot filmstrip."""
        return self._rows(
            "SELECT url, max(company) AS company, max(role_title) AS role_title, run_id, "
            "count(*) AS steps, max(created_at) AS finished_at "
            "FROM apply_steps GROUP BY url, run_id ORDER BY max(created_at) DESC LIMIT %s", (limit,))

    def apply_steps(self, url: str, run_id: str = "") -> list[dict[str, Any]]:
        if run_id:
            return self._rows(
                "SELECT id,step_no,label,note,created_at,(shot IS NOT NULL) AS has_shot FROM apply_steps "
                "WHERE url=%s AND run_id=%s ORDER BY step_no", (url, run_id))
        return self._rows(
            "SELECT id,step_no,label,note,created_at,(shot IS NOT NULL) AS has_shot FROM apply_steps "
            "WHERE url=%s ORDER BY step_no", (url,))

    def apply_step_shot(self, step_id: int) -> bytes | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT shot FROM apply_steps WHERE id=%s", (step_id,))
            row = cur.fetchone()
            return bytes(row[0]) if row and row[0] is not None else None

    # ------------------------------------------------------------------ apply-requests queue
    # (URLs the user queues from the Streamlit control panel; the LOCAL runner processes them.)
    def add_apply_requests(self, items: list[dict[str, Any]]) -> int:
        """items: [{url, title?, company?, source?, auto_submit?}]. Skips URLs already open in the queue.
        Returns how many rows were added."""
        added = 0
        with self.conn.cursor() as cur:
            for it in items:
                url = (it.get("url") or "").strip()
                if not url:
                    continue
                cur.execute(
                    "INSERT INTO apply_requests (url,title,company,source,auto_submit,status) "
                    "VALUES (%s,%s,%s,%s,%s,'queued') "
                    "ON CONFLICT (url) WHERE status IN ('queued','processing') DO NOTHING RETURNING id",
                    (url, it.get("title", ""), it.get("company", ""),
                     it.get("source", "pasted"), bool(it.get("auto_submit", False)))
                )
                added += int(cur.fetchone() is not None)
        return added

    def apply_requests(self, status: str = "queued", limit: int = 100) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT id,url,title,company,source,auto_submit,status,note,requested_at,processed_at "
            "FROM apply_requests WHERE status=%s ORDER BY id ASC LIMIT %s", (status, limit))

    def apply_requests_rows(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT id,url,title,company,source,auto_submit,status,note,requested_at,processed_at "
            "FROM apply_requests ORDER BY id DESC LIMIT %s", (limit,))

    def set_apply_request_status(self, req_id: int, status: str, note: str = "") -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE apply_requests SET status=%s, note=%s, "
                "processed_at = CASE WHEN %s IN ('done','needs_submit','skipped','error') THEN now() ELSE processed_at END "
                "WHERE id=%s", (status, note[:400], status, req_id))

    def clear_apply_requests(self, which: str = "done") -> int:
        """which = 'done' (finished), 'queued' (pending), or 'all'."""
        with self.conn.cursor() as cur:
            if which == "all":
                cur.execute("DELETE FROM apply_requests")
            elif which == "queued":
                cur.execute("DELETE FROM apply_requests WHERE status='queued'")
            else:
                cur.execute("DELETE FROM apply_requests WHERE status IN ('done','skipped','error','needs_submit')")
            return cur.rowcount or 0

    def best_jobs(self, *, limit: int = 12, max_age_days: int = 3, min_fit: int = 0,
                  fresh_only: bool = True) -> list[dict[str, Any]]:
        """The best jobs for the run report / Telegram, ranked by IMPORTANCE:
        top100 bucket > any bucket > fit score > recency. Prefers fresh (last few days) roles but
        the caller can widen the window if a run finds nothing fresh, so alerts are never empty."""
        from datetime import timedelta
        params: list[Any] = []
        where = ["is_target=TRUE"]
        if min_fit:
            where.append("fit_score >= %s"); params.append(min_fit)
        if fresh_only:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).replace(microsecond=0).isoformat()
            where.append("first_seen_at >= %s"); params.append(cutoff)
        params.append(limit)
        return self._rows(
            "SELECT dedupe_key,title,company,location,locations,fit_score,fit_reasoning,url,in_bucket,"
            "bucket_tier,category,sector,cv_keywords,country,visa_sponsorship,geo_score,notified,"
            "posted_date,first_seen_at "
            "FROM jobs WHERE " + " AND ".join(where) +
            # importance: bucket-list first, then UK>EU>world + sponsor (geo_score), then fit, then fresh
            " ORDER BY (bucket_tier='top100') DESC, in_bucket DESC, geo_score DESC, fit_score DESC, "
            "first_seen_at DESC LIMIT %s", tuple(params))

    def status_counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self._rows("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}

    def source_counts(self) -> dict[str, int]:
        return {r["source"]: r["n"] for r in self._rows("SELECT source, COUNT(*) n FROM jobs GROUP BY source")}

    def recent_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT %s", (limit,))

    def close(self) -> None:
        self.conn.close()
