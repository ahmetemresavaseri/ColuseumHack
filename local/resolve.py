"""End-to-end: 5 inputs -> allocation + pricing + calendar slots.

Loads company.json from repo root, applies allocator + pricing, optionally
queries Google Calendar for available slots in a window derived from `when`.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import allocator, pricing

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANY_JSON = REPO_ROOT / "company.json"


def load_company() -> dict[str, Any]:
    return json.loads(COMPANY_JSON.read_text(encoding="utf-8"))


def _resolve_calendar_id(crew_id: str, raw_id: str | None) -> str | None:
    """If the company.json crew calendar id is a placeholder like
    "<CALENDAR_ID_1>", try to substitute it from environment variables.
    """
    if raw_id and not raw_id.startswith("<"):
        return raw_id
    env_map = {
        "crew-zh-1": "CALENDAR_ID_CREW_ZH_1",
        "crew-zh-2": "CALENDAR_ID_CREW_ZH_2",
    }
    env_key = env_map.get(crew_id)
    if env_key:
        v = os.environ.get(env_key)
        if v:
            return v
    return None


def parse_when(when: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Parse `when` into a (start, end) window in the given timezone.

    Accepts:
      - "YYYY-MM-DD"             → that day, 00:00 → +1 day 00:00
      - "YYYY-MM-DD..YYYY-MM-DD" → inclusive range
      - "next N days"            → today → today + N
      - default                  → today → today + 7 days
    """
    when = (when or "").strip().lower()

    if m := re.match(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$", when):
        s = datetime.fromisoformat(m.group(1)).replace(tzinfo=tz)
        e = datetime.fromisoformat(m.group(2)).replace(tzinfo=tz) + timedelta(days=1)
        return s, e

    if re.match(r"^\d{4}-\d{2}-\d{2}$", when):
        s = datetime.fromisoformat(when).replace(tzinfo=tz)
        return s, s + timedelta(days=1)

    if m := re.match(r"^next\s+(\d+)\s+days?$", when):
        s = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        return s, s + timedelta(days=int(m.group(1)))

    s = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return s, s + timedelta(days=7)


def resolve(inputs: dict[str, Any], *, check_calendar: bool = True,
            company: dict[str, Any] | None = None) -> dict[str, Any]:
    company = company or load_company()

    alloc = allocator.allocate(inputs, company)
    price = pricing.compute(alloc, inputs, company)

    out: dict[str, Any] = {
        "inputs": dict(inputs),
        "allocation": alloc,
        "pricing": price,
        "calendar": {"checked": False, "availableSlots": [], "warning": None},
    }

    if not check_calendar:
        return out

    tz = ZoneInfo(company.get("timezone", "Europe/Zurich"))
    start, end = parse_when(inputs.get("when", ""), tz)
    cal_id = _resolve_calendar_id(alloc["crew"]["id"], alloc["crew"].get("googleCalendarId"))

    if not cal_id:
        out["calendar"]["warning"] = (
            f"no calendar id for {alloc['crew']['id']} — set env var or fill company.json"
        )
        return out

    try:
        from . import calendar_client
        slots = calendar_client.get_availability(
            calendar_id=cal_id,
            start=start, end=end,
            duration_minutes=alloc["wall_clock_minutes"],
            business_hours=company["business_hours"],
        )
        out["calendar"] = {
            "checked": True,
            "calendarId": cal_id,
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "availableSlots": slots,
            "warning": None,
        }
    except Exception as exc:
        out["calendar"]["warning"] = f"calendar lookup failed: {exc!r}"

    return out
