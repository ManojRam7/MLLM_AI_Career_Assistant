"""App-side Google Calendar + Drive sync for the application tracker (runs INSIDE the app / the
GitHub Actions pipeline, both of which can reach Supabase).

Auth = a Google SERVICE ACCOUNT (headless, no interactive OAuth):
  - GOOGLE_SERVICE_ACCOUNT_JSON   raw JSON string of the service-account key   (or)
  - GOOGLE_SERVICE_ACCOUNT_FILE   path to the key file
  - GOOGLE_CALENDAR_ID            a calendar SHARED with the service-account email
                                  ('Make changes to events')
  - GOOGLE_DRIVE_FOLDER_ID        a Drive folder SHARED with the service-account email (Editor)

Note: a service account CANNOT read a personal @gmail.com inbox (that needs an OAuth user token or
Google Workspace domain delegation), so Gmail stays a paste-a-link field in the tracker.
Everything imports the Google libs lazily and fails with a clear message if unconfigured."""
from __future__ import annotations

import datetime as dt
import io
import json
import os

_CAL_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def configured() -> bool:
    return bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE"))


def _creds(scopes: list[str]):
    from google.oauth2 import service_account
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if path and os.path.exists(path):
        return service_account.Credentials.from_service_account_file(path, scopes=scopes)
    raise RuntimeError("No Google service-account credential set (GOOGLE_SERVICE_ACCOUNT_JSON).")


def _event_id(app_id, date_str: str) -> str:
    """Deterministic Calendar event id (chars a-v + 0-9 only) so re-syncing UPDATES, never duplicates."""
    d = "".join(ch for ch in str(date_str)[:10] if ch.isdigit())
    return f"jobapp{app_id}d{d}"


def sync_calendar(apps: list[dict], calendar_id: str | None = None) -> tuple[int, str]:
    """Create/update an all-day event for every application that has a next_action_date
    (interview / assessment / follow-up). Idempotent. Returns (events_synced, message)."""
    calendar_id = calendar_id or os.environ.get("GOOGLE_CALENDAR_ID", "primary")
    from googleapiclient.discovery import build
    svc = build("calendar", "v3", credentials=_creds(_CAL_SCOPES), cache_discovery=False)
    n, errs = 0, []
    for a in apps:
        d = str(a.get("next_action_date") or "")[:10]
        if len(d) != 10:
            continue
        try:
            end = (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()
        except Exception:
            continue
        label = a.get("next_action") or a.get("status", "follow up")
        summary = f"{label}: {a.get('company','')} — {a.get('role_title','')}".strip(" —:")
        body = {"id": _event_id(a.get("id"), d), "summary": summary,
                "description": " ".join(x for x in [a.get("country", ""), a.get("city", ""),
                                                    a.get("source_url", ""), a.get("email_link", "")] if x),
                "start": {"date": d}, "end": {"date": end}}
        try:
            svc.events().insert(calendarId=calendar_id, body=body).execute()
            n += 1
        except Exception:
            try:      # already exists -> update in place
                svc.events().update(calendarId=calendar_id, eventId=body["id"], body=body).execute()
                n += 1
            except Exception as e2:
                errs.append(str(e2)[:80])
    return n, ("; ".join(errs[:2]) if errs else "ok")


def backup_to_drive(csv_text: str, folder_id: str | None = None,
                    filename: str = "applications_backup.csv") -> str:
    """Upload (or update in place) the tracker CSV into the shared Drive folder. Returns a status word."""
    folder_id = folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
    svc = build("drive", "v3", credentials=_creds(_DRIVE_SCOPES), cache_discovery=False)
    media = MediaIoBaseUpload(io.BytesIO(csv_text.encode("utf-8")), mimetype="text/csv", resumable=False)
    q = f"name = '{filename}' and trashed = false"
    if folder_id:
        q += f" and '{folder_id}' in parents"
    found = svc.files().list(q=q, fields="files(id)", pageSize=1,
                             supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
    if found:
        svc.files().update(fileId=found[0]["id"], media_body=media, supportsAllDrives=True).execute()
        return "updated"
    meta = {"name": filename}
    if folder_id:
        meta["parents"] = [folder_id]
    svc.files().create(body=meta, media_body=media, fields="id", supportsAllDrives=True).execute()
    return "created"
