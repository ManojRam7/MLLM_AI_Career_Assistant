"""Supabase / Postgres storage + tracker + run history.
Connect DBeaver (or the dashboard) to the same SUPABASE_DB_URL."""
from __future__ import annotations

import json
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
    status       TEXT DEFAULT 'new',
    is_custom    BOOLEAN DEFAULT FALSE,
    in_bucket    BOOLEAN DEFAULT FALSE,
    notes        TEXT DEFAULT '',
    applied_at   TEXT DEFAULT '',
    cv_path      TEXT,
    cover_path   TEXT,
    gaps         JSONB DEFAULT '[]',
    first_seen_at TEXT,
    last_seen_at  TEXT
);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS in_bucket BOOLEAN DEFAULT FALSE;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS applied_at TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_fit_idx ON jobs(fit_score DESC);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id          BIGSERIAL PRIMARY KEY,
    run_at      TEXT,
    mode        TEXT,
    discovered  INTEGER, targets INTEGER, rejected INTEGER,
    scored      INTEGER, tailored INTEGER, stored_new INTEGER,
    llm_note    TEXT
);
"""

UPSERT = """
INSERT INTO jobs (dedupe_key,title,company,location,url,description,posted_date,salary,remote,
                  source,source_query,seniority,is_target,in_bucket,first_seen_at,last_seen_at,is_custom,status)
VALUES (%(dedupe_key)s,%(title)s,%(company)s,%(location)s,%(url)s,%(description)s,%(posted_date)s,
        %(salary)s,%(remote)s,%(source)s,%(source_query)s,%(seniority)s,%(is_target)s,%(in_bucket)s,
        %(first_seen_at)s,%(last_seen_at)s,%(is_custom)s,%(status)s)
ON CONFLICT (dedupe_key) DO UPDATE SET
    last_seen_at = EXCLUDED.last_seen_at,
    in_bucket = jobs.in_bucket OR EXCLUDED.in_bucket,
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
        return j.dedupe_key

    def update(self, dedupe_key: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = %s" for k in fields)
        vals = [json.dumps(v) if k == "gaps" else v for k, v in fields.items()]
        with self.conn.cursor() as cur:
            cur.execute(f"UPDATE jobs SET {sets} WHERE dedupe_key = %s", (*vals, dedupe_key))

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
                "INSERT INTO pipeline_runs (run_at,mode,discovered,targets,rejected,scored,tailored,stored_new,llm_note)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (_now(), summary.get("mode"), summary.get("discovered"), summary.get("targets"),
                 summary.get("rejected"), summary.get("scored"), summary.get("tailored"),
                 summary.get("stored_new"), summary.get("llm_note", "")))

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
            "ORDER BY in_bucket DESC LIMIT %s", (limit,))

    def jobs_to_tailor(self, threshold: int, limit: int = 6) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT dedupe_key,title,company,location,description FROM jobs "
            "WHERE (cv_path IS NULL OR cv_path='') AND is_target=TRUE "
            "AND (fit_score >= %s OR is_custom=TRUE) "
            "ORDER BY in_bucket DESC, fit_score DESC LIMIT %s", (threshold, limit))

    def digest(self, min_fit: int = 70, limit: int = 20) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT title,company,location,url,fit_score FROM jobs WHERE fit_score >= %s "
            "ORDER BY first_seen_at DESC LIMIT %s", (min_fit, limit))

    def all_jobs(self, limit: int = 2000) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT dedupe_key,title,company,location,source,in_bucket,fit_score,seniority,status,"
            "notes,applied_at,url,cv_path,cover_path,fit_reasoning,ghost_flag,posted_date,first_seen_at "
            "FROM jobs ORDER BY in_bucket DESC, fit_score DESC, first_seen_at DESC LIMIT %s", (limit,))

    def status_counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self._rows("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}

    def source_counts(self) -> dict[str, int]:
        return {r["source"]: r["n"] for r in self._rows("SELECT source, COUNT(*) n FROM jobs GROUP BY source")}

    def recent_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT %s", (limit,))

    def close(self) -> None:
        self.conn.close()
