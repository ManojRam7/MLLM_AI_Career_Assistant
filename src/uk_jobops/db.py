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
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS recommendations TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cover_text TEXT;
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
"""

UPSERT = """
INSERT INTO jobs (dedupe_key,title,company,location,locations,url,description,posted_date,salary,remote,
                  source,source_query,seniority,category,is_target,in_bucket,bucket_tier,first_seen_at,last_seen_at,is_custom,status)
VALUES (%(dedupe_key)s,%(title)s,%(company)s,%(location)s,%(locations)s,%(url)s,%(description)s,%(posted_date)s,
        %(salary)s,%(remote)s,%(source)s,%(source_query)s,%(seniority)s,%(category)s,%(is_target)s,%(in_bucket)s,%(bucket_tier)s,
        %(first_seen_at)s,%(last_seen_at)s,%(is_custom)s,%(status)s)
ON CONFLICT (dedupe_key) DO UPDATE SET
    last_seen_at = EXCLUDED.last_seen_at,
    in_bucket = jobs.in_bucket OR EXCLUDED.in_bucket,
    category = COALESCE(NULLIF(EXCLUDED.category,''), jobs.category),
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

        self.conn = psycopg.connect(u, autocommit=True)

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

    def jobs_to_notify(self, min_fit: int = 75, limit: int = 10) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT dedupe_key,title,company,location,locations,fit_score,fit_reasoning,url,in_bucket,bucket_tier,category "
            "FROM jobs WHERE notified=FALSE AND is_target=TRUE AND fit_score >= %s "
            "ORDER BY (bucket_tier='top100') DESC, fit_score DESC LIMIT %s", (min_fit, limit))

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

    def jobs_needing_score(self, limit: int = 40) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT dedupe_key,title,company,location,description FROM jobs "
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
            "SELECT dedupe_key,title,company,location,locations,source,in_bucket,bucket_tier,category,fit_score,seniority,status,"
            "tracked,is_custom,notes,applied_at,url,cv_path,cover_path,fit_reasoning,ghost_flag,posted_date,first_seen_at "
            "FROM jobs ORDER BY (bucket_tier='top100') DESC, in_bucket DESC, fit_score DESC, first_seen_at DESC LIMIT %s",
            (limit,))

    def recommendations_list(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT dedupe_key,title,company,category,location,fit_score,in_bucket,bucket_tier,"
            "fit_reasoning,url,recommendations,cover_text "
            "FROM jobs WHERE recommendations IS NOT NULL "
            "ORDER BY (bucket_tier='top100') DESC, in_bucket DESC, fit_score DESC LIMIT %s", (limit,))

    def status_counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self._rows("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}

    def source_counts(self) -> dict[str, int]:
        return {r["source"]: r["n"] for r in self._rows("SELECT source, COUNT(*) n FROM jobs GROUP BY source")}

    def recent_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT %s", (limit,))

    def close(self) -> None:
        self.conn.close()
