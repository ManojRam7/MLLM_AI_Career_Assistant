"""Job APPLICATION TRACKER — a Huntr-style board for the roles Manoj has actually applied to.

DESIGN NOTE (isolation is the whole point): this lives in its OWN Supabase table, `applications`,
completely separate from the pipeline's `jobs` table. Nothing in the discovery/scoring/cleanup
pipeline reads or writes this table, so no run — and no future feature — can ever move, overwrite,
or delete a tracked application. Manual, durable, yours. Every write bumps updated_at; nothing here
is ever auto-purged.

Contains the DB layer (Tracker) plus pure, DB-free helpers (csv/ics/day) that are unit-tested."""
from __future__ import annotations

import csv
import datetime as dt
import io
from typing import Any

# the Huntr-style pipeline stages, in board order
STATUSES = ["applied", "assessment", "assessment_cleared", "interview", "offer", "rejected"]
STATUS_LABEL = {
    "applied": "Applied", "assessment": "Assessment", "assessment_cleared": "Assessment Cleared",
    "interview": "Interview", "offer": "Offer", "rejected": "Rejected",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id            BIGSERIAL PRIMARY KEY,
    company       TEXT NOT NULL,
    role_title    TEXT NOT NULL,
    country       TEXT DEFAULT 'United Kingdom',
    city          TEXT DEFAULT '',
    source_url    TEXT DEFAULT '',
    status        TEXT DEFAULT 'applied',
    applied_date  DATE DEFAULT current_date,
    next_action        TEXT DEFAULT '',
    next_action_date   DATE,
    salary        TEXT DEFAULT '',
    contact       TEXT DEFAULT '',
    notes         TEXT DEFAULT '',
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS applications_status_idx ON applications(status);
CREATE INDEX IF NOT EXISTS applications_applied_idx ON applications(applied_date DESC);
"""

_FIELDS = ("company", "role_title", "country", "city", "source_url", "status", "applied_date",
           "next_action", "next_action_date", "salary", "contact", "notes")


def day_name(d: Any) -> str:
    """Weekday name for a date/ISO-string (e.g. 'Monday'); '' if unparseable."""
    try:
        if isinstance(d, dt.date):
            return d.strftime("%A")
        return dt.date.fromisoformat(str(d)[:10]).strftime("%A")
    except Exception:
        return ""


def rows_to_csv(rows: list[dict]) -> str:
    """Export the tracker to CSV text (safe backup / Google Drive upload)."""
    cols = ["id", "company", "role_title", "country", "city", "status", "applied_date", "day",
            "next_action", "next_action_date", "salary", "contact", "source_url", "notes"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        row = dict(r)
        row["day"] = day_name(r.get("applied_date"))
        w.writerow(row)
    return buf.getvalue()


def _ics_dt(d: Any) -> str:
    try:
        return (d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])).strftime("%Y%m%d")
    except Exception:
        return ""


def rows_to_ics(rows: list[dict]) -> str:
    """Build an .ics calendar of upcoming actions (next_action_date) + interview/assessment dates,
    as all-day events — importable into Google Calendar / Apple Calendar."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//uk_jobops//application-tracker//EN",
             "CALSCALE:GREGORIAN"]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for r in rows:
        d = _ics_dt(r.get("next_action_date"))
        if not d:
            continue
        end = (dt.date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:8]}") + dt.timedelta(days=1)).strftime("%Y%m%d")
        label = (r.get("next_action") or STATUS_LABEL.get(r.get("status", ""), "Follow up"))
        summary = f"{label}: {r.get('company','')} — {r.get('role_title','')}".strip(" —:")
        uid = f"app-{r.get('id','x')}-{d}@uk_jobops"
        desc = " ".join(x for x in [r.get("country", ""), r.get("city", ""), r.get("source_url", "")] if x)
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{d}", f"DTEND;VALUE=DATE:{end}",
                  f"SUMMARY:{_ics_escape(summary)}", f"DESCRIPTION:{_ics_escape(desc)}",
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def board_stats(rows: list[dict]) -> dict[str, int]:
    out = {s: 0 for s in STATUSES}
    for r in rows:
        s = r.get("status", "applied")
        out[s] = out.get(s, 0) + 1
    out["total"] = len(rows)
    out["active"] = sum(1 for r in rows if r.get("status") not in ("rejected",))
    return out


class Tracker:
    """DB layer for the isolated `applications` table. Reuses the same Supabase connection style as
    Store (autocommit + prepare_threshold=None for the transaction pooler)."""

    def __init__(self, db_url: str):
        import psycopg
        self.conn = psycopg.connect((db_url or "").strip(), autocommit=True, prepare_threshold=None)

    def init_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(SCHEMA)

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def add(self, **f: Any) -> int:
        data = {k: f.get(k) for k in _FIELDS if f.get(k) is not None}
        data.setdefault("company", "Unknown")
        data.setdefault("role_title", "Role")
        data.setdefault("status", "applied")
        cols = ", ".join(data)
        ph = ", ".join(f"%({k})s" for k in data)
        with self.conn.cursor() as cur:
            cur.execute(f"INSERT INTO applications ({cols}) VALUES ({ph}) RETURNING id", data)
            return cur.fetchone()[0]

    def update(self, app_id: int, **f: Any) -> None:
        data = {k: v for k, v in f.items() if k in _FIELDS}
        if not data:
            return
        sets = ", ".join(f"{k} = %s" for k in data) + ", updated_at = now()"
        with self.conn.cursor() as cur:
            cur.execute(f"UPDATE applications SET {sets} WHERE id = %s", (*data.values(), app_id))

    def set_status(self, app_id: int, status: str) -> None:
        if status in STATUSES:
            self.update(app_id, status=status)

    def delete(self, app_id: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM applications WHERE id = %s", (app_id,))

    def list_all(self) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT id,company,role_title,country,city,source_url,status,applied_date,next_action,"
            "next_action_date,salary,contact,notes,created_at,updated_at "
            "FROM applications ORDER BY applied_date DESC NULLS LAST, id DESC")

    def by_status(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list] = {s: [] for s in STATUSES}
        for r in self.list_all():
            out.setdefault(r.get("status", "applied"), []).append(r)
        return out

    def stats(self) -> dict[str, int]:
        return board_stats(self.list_all())

    def close(self) -> None:
        self.conn.close()
