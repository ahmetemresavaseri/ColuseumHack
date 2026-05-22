"""Google Calendar tool — Lambda-side wrapper.

Two operations the voice agent can call:
  - check_availability(date_iso, duration_minutes, ...) → top-N free slots
  - book_appointment(start_iso, end_iso, summary, ...)  → creates the event

Auth: service-account JSON, supplied via env var GOOGLE_SA_JSON_B64
(base64-encoded JSON; deploy_lambda.py reads local/credentials/service-account.json
and sets this at deploy time). Falls back to GOOGLE_APPLICATION_CREDENTIALS for
local runs.

Defaults come from env:
  GOOGLE_CALENDAR_ID    — fallback calendar id when caller omits one
  ATRIUM_TIMEZONE       — IANA tz, defaults to "Europe/Zurich"
  ATRIUM_BUSINESS_HOURS_JSON — e.g. {"weekdays":[0,1,2,3,4],"start_hour":8,"end_hour":18,"slot_step_minutes":30}

google-auth + google-api-python-client are lazy-imported so unit tests on the
plain Lambda runtime don't crash before they're vendored in.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime, time, timedelta
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BUSINESS_HOURS: dict[str, Any] = {
    "weekdays": [0, 1, 2, 3, 4],
    "start_hour": 8,
    "end_hour": 18,
    "slot_step_minutes": 30,
}

_SERVICE_CACHE: Any = None


def _zoneinfo(name: str):
    from zoneinfo import ZoneInfo  # stdlib in py3.9+
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _load_credentials():
    from google.oauth2 import service_account  # type: ignore

    raw_b64 = os.environ.get("GOOGLE_SA_JSON_B64")
    if raw_b64:
        info = json.loads(base64.b64decode(raw_b64).decode("utf-8"))
        return service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/calendar"]
        )
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if path and os.path.isfile(path):
        return service_account.Credentials.from_service_account_file(
            path, scopes=["https://www.googleapis.com/auth/calendar"]
        )
    raise RuntimeError(
        "no Google credentials found — set GOOGLE_SA_JSON_B64 or GOOGLE_APPLICATION_CREDENTIALS"
    )


def _service():
    global _SERVICE_CACHE
    if _SERVICE_CACHE is not None:
        return _SERVICE_CACHE
    from googleapiclient.discovery import build  # type: ignore
    _SERVICE_CACHE = build(
        "calendar", "v3", credentials=_load_credentials(), cache_discovery=False
    )
    return _SERVICE_CACHE


def _business_hours() -> dict[str, Any]:
    raw = os.environ.get("ATRIUM_BUSINESS_HOURS_JSON")
    if not raw:
        return dict(DEFAULT_BUSINESS_HOURS)
    try:
        return {**DEFAULT_BUSINESS_HOURS, **json.loads(raw)}
    except Exception:
        return dict(DEFAULT_BUSINESS_HOURS)


def _default_calendar_id() -> str | None:
    return os.environ.get("GOOGLE_CALENDAR_ID") or None


def _default_tz_name() -> str:
    return os.environ.get("ATRIUM_TIMEZONE", "Europe/Zurich")


# ---- date parsing ----------------------------------------------------------

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _parse_date_window(date_iso: str | None, tz) -> tuple[datetime, datetime]:
    """Parse a date-ish string into a [start, end) window.

    Accepts:
      "YYYY-MM-DD"            → that day 00:00–24:00
      "YYYY-MM-DD..YYYY-MM-DD" → inclusive range
      anything else / empty    → today + next 7 days
    """
    s = (date_iso or "").strip()

    if ".." in s:
        a, _, b = s.partition("..")
        ma, mb = _DATE_RE.match(a or ""), _DATE_RE.match(b or "")
        if ma and mb:
            start = datetime(int(ma.group(1)), int(ma.group(2)), int(ma.group(3)), tzinfo=tz)
            end = datetime(int(mb.group(1)), int(mb.group(2)), int(mb.group(3)), tzinfo=tz) + timedelta(days=1)
            return start, end

    if m := _DATE_RE.match(s):
        start = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=tz)
        return start, start + timedelta(days=1)

    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def _iter_business_windows(start: datetime, end: datetime,
                           bh: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    weekdays = set(bh.get("weekdays", [0, 1, 2, 3, 4]))
    sh = int(bh.get("start_hour", 8))
    eh = int(bh.get("end_hour", 18))
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
    remainder = dt.minute % step_minutes
    if remainder == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt
    add = step_minutes - remainder
    return (dt + timedelta(minutes=add)).replace(second=0, microsecond=0)


def _human_slot(start: datetime) -> str:
    """A spoken-friendly label for the caller, e.g. 'Wednesday May 29th at 9 AM'."""
    day = start.strftime("%A %B %-d") if hasattr(start, "strftime") else str(start)
    # Windows strftime has no %-d, so DIY:
    day_name = start.strftime("%A")
    month = start.strftime("%B")
    day_num = start.day
    suffix = "th"
    if day_num % 100 not in (11, 12, 13):
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day_num % 10, "th")
    hour = start.hour
    minute = start.minute
    ampm = "AM" if hour < 12 else "PM"
    h12 = ((hour - 1) % 12) + 1
    time_str = f"{h12}:{minute:02d} {ampm}" if minute else f"{h12} {ampm}"
    return f"{day_name} {month} {day_num}{suffix} at {time_str}"


# ---- public API ------------------------------------------------------------


def check_availability(
    date_iso: str | None,
    duration_minutes: int,
    *,
    calendar_id: str | None = None,
    max_slots: int = 3,
    business_hours: dict[str, Any] | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    """Return up to `max_slots` free windows of length >= duration_minutes.

    Soft-fail shape: on missing creds or API errors, returns
    `{"status": "calendar_not_configured", ...}` instead of raising, so the
    voice agent stays alive and can apologize gracefully.
    """
    cal_id = calendar_id or _default_calendar_id()
    if not cal_id:
        return {"status": "calendar_not_configured", "reason": "no_calendar_id", "slots": []}

    tz_name = timezone_name or _default_tz_name()
    tz = _zoneinfo(tz_name)
    start, end = _parse_date_window(date_iso, tz)
    bh = business_hours or _business_hours()
    duration = timedelta(minutes=max(15, int(duration_minutes or 60)))

    try:
        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": cal_id}],
        }
        resp = _service().freebusy().query(body=body).execute()
    except Exception as exc:
        logger.warning("freebusy query failed: %s", exc)
        return {"status": "calendar_error", "reason": str(exc)[:200], "slots": []}

    busy_raw = resp.get("calendars", {}).get(cal_id, {}).get("busy", [])
    busy = [
        (datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
         datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
        for b in busy_raw
    ]

    step = int(bh.get("slot_step_minutes", 30))
    slots: list[dict[str, str]] = []

    for ws, we in _iter_business_windows(start, end, bh):
        cursor = _round_up(ws, step)
        window_busy = sorted(
            (b for b in busy if b[1] > ws and b[0] < we), key=lambda x: x[0]
        )
        for bstart, bend in window_busy:
            free_end = min(bstart, we)
            while cursor + duration <= free_end and len(slots) < max_slots:
                slots.append({
                    "start": cursor.isoformat(),
                    "end": (cursor + duration).isoformat(),
                    "human": _human_slot(cursor),
                })
                cursor = _round_up(cursor + duration, step)
            cursor = max(cursor, bend)
            cursor = _round_up(cursor, step)
            if len(slots) >= max_slots:
                break
        while cursor + duration <= we and len(slots) < max_slots:
            slots.append({
                "start": cursor.isoformat(),
                "end": (cursor + duration).isoformat(),
                "human": _human_slot(cursor),
            })
            cursor = _round_up(cursor + duration, step)
        if len(slots) >= max_slots:
            break

    return {
        "status": "ok" if slots else "no_slots",
        "calendarId": cal_id,
        "timezone": tz_name,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "slots": slots,
    }


def book_appointment(
    start_iso: str,
    end_iso: str,
    summary: str,
    *,
    location: str = "",
    description: str = "",
    calendar_id: str | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    """Create a calendar event. Soft-fails like check_availability."""
    cal_id = calendar_id or _default_calendar_id()
    if not cal_id:
        return {"status": "calendar_not_configured", "reason": "no_calendar_id"}

    tz_name = timezone_name or _default_tz_name()
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except Exception as exc:
        return {"status": "bad_input", "reason": f"could not parse start/end: {exc}"}

    if start.tzinfo is None or end.tzinfo is None:
        tz = _zoneinfo(tz_name)
        start = start.replace(tzinfo=tz) if start.tzinfo is None else start
        end = end.replace(tzinfo=tz) if end.tzinfo is None else end

    event_body = {
        "summary": summary or "Atrium booking",
        "description": description,
        "location": location,
        "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
        "end":   {"dateTime": end.isoformat(),   "timeZone": tz_name},
    }
    try:
        created = _service().events().insert(calendarId=cal_id, body=event_body).execute()
    except Exception as exc:
        logger.warning("event insert failed: %s", exc)
        return {"status": "calendar_error", "reason": str(exc)[:200]}

    return {
        "status": "booked",
        "calendarId": cal_id,
        "eventId": created.get("id", ""),
        "htmlLink": created.get("htmlLink", ""),
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
