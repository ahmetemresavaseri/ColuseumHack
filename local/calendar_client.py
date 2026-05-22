"""Google Calendar client: availability scan + event creation.

Auth: Service-Account JSON key, path in env var GOOGLE_APPLICATION_CREDENTIALS.
Scope: https://www.googleapis.com/auth/calendar
"""
from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone
from typing import Any

# Lazy imports so allocator+pricing remain usable even when google-* libs
# are not installed yet.
_SERVICE_CACHE: Any = None


def _service():
    global _SERVICE_CACHE
    if _SERVICE_CACHE is not None:
        return _SERVICE_CACHE

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not key_path:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS not set. "
            "Copy local/.env.example to local/.env and fill in the path."
        )
    if not os.path.isfile(key_path):
        raise RuntimeError(f"Service-account JSON not found at: {key_path}")

    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=["https://www.googleapis.com/auth/calendar"]
    )
    _SERVICE_CACHE = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _SERVICE_CACHE


def _iter_business_windows(start: datetime, end: datetime,
                           business_hours: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    """Yield (window_start, window_end) for each business day in [start, end)."""
    weekdays = set(business_hours.get("weekdays", [0, 1, 2, 3, 4]))
    sh = int(business_hours.get("start_hour", 8))
    eh = int(business_hours.get("end_hour", 18))

    out: list[tuple[datetime, datetime]] = []
    day = start.date()
    end_day = end.date()
    while day <= end_day:
        if day.weekday() in weekdays:
            tz = start.tzinfo
            ws = datetime.combine(day, time(sh, 0), tzinfo=tz)
            we = datetime.combine(day, time(eh, 0), tzinfo=tz)
            ws = max(ws, start)
            we = min(we, end)
            if ws < we:
                out.append((ws, we))
        day += timedelta(days=1)
    return out


def _round_up(dt: datetime, step_minutes: int) -> datetime:
    if step_minutes <= 1:
        return dt
    minute = dt.minute
    remainder = minute % step_minutes
    if remainder == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt
    add = step_minutes - remainder
    return (dt + timedelta(minutes=add)).replace(second=0, microsecond=0)


def get_availability(calendar_id: str, start: datetime, end: datetime,
                     duration_minutes: int, business_hours: dict[str, Any],
                     max_slots: int = 8) -> list[dict[str, str]]:
    """Return a list of free [start, end] windows of length >= duration_minutes.

    Honors business_hours (weekdays + start_hour/end_hour) and slot_step_minutes.
    Uses Google Calendar freebusy API to query busy intervals.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware datetimes")

    body = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "items": [{"id": calendar_id}],
    }
    resp = _service().freebusy().query(body=body).execute()
    busy_raw = resp.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    busy = [
        (datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
         datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
        for b in busy_raw
    ]

    step = int(business_hours.get("slot_step_minutes", 30))
    duration = timedelta(minutes=int(duration_minutes))

    slots: list[dict[str, str]] = []
    for ws, we in _iter_business_windows(start, end, business_hours):
        cursor = _round_up(ws, step)
        # Build sorted busy intervals overlapping this window.
        window_busy = sorted(
            (b for b in busy if b[1] > ws and b[0] < we),
            key=lambda x: x[0],
        )
        for bstart, bend in window_busy:
            free_end = min(bstart, we)
            if cursor + duration <= free_end:
                slots.append({"start": cursor.isoformat(), "end": (cursor + duration).isoformat()})
                if len(slots) >= max_slots:
                    return slots
            cursor = max(cursor, bend)
            cursor = _round_up(cursor, step)
        # Tail of the window.
        if cursor + duration <= we:
            slots.append({"start": cursor.isoformat(), "end": (cursor + duration).isoformat()})
            if len(slots) >= max_slots:
                return slots
    return slots


def create_event(calendar_id: str, summary: str, start: datetime, end: datetime,
                 description: str = "", location: str = "") -> dict[str, str]:
    """Create a calendar event. Returns {eventId, htmlLink}."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware datetimes")
    event_body = {
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start.isoformat()},
        "end":   {"dateTime": end.isoformat()},
    }
    created = _service().events().insert(calendarId=calendar_id, body=event_body).execute()
    return {"eventId": created.get("id", ""), "htmlLink": created.get("htmlLink", "")}
