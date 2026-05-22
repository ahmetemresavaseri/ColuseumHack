"""CLI smoke test for the resolve pipeline.

Usage:
  python -m local.smoke --when 2026-05-29 --where "8001 Zurich" \
                        --m2 75 --rooms 3 --cat move_out_cleaning
  python -m local.smoke ... --no-calendar       # skip Google Calendar lookup
  python -m local.smoke ... --create-event      # also create event for first free slot
  python -m local.smoke ... --bathrooms 2 --urgency urgent --condition heavy \
                            --addons oven blinds
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Force UTF-8 stdout/stderr on Windows so non-ASCII chars in output don't crash.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# Allow running as `python local/smoke.py` (no package context).
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from local import resolve  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Atrium resolve smoke test")
    p.add_argument("--when", required=True, help="YYYY-MM-DD or YYYY-MM-DD..YYYY-MM-DD or 'next 7 days'")
    p.add_argument("--where", required=True, help="Address or postcode (e.g. '8001 Zurich')")
    p.add_argument("--m2", type=float, required=True, help="Area in square meters")
    p.add_argument("--rooms", type=int, required=True, help="Number of rooms")
    p.add_argument("--cat", required=True, help="service_type key (e.g. move_out_cleaning)")
    p.add_argument("--bathrooms", type=int, default=None)
    p.add_argument("--urgency", default=None, help="flexible|standard|urgent|same_day|weekend")
    p.add_argument("--condition", default=None, help="light|normal|heavy|unknown")
    p.add_argument("--addons", nargs="*", default=[], help="space-separated addon keys")
    p.add_argument("--no-calendar", action="store_true", help="skip Google Calendar lookup")
    p.add_argument("--create-event", action="store_true",
                   help="after resolving, create a Calendar event in the first free slot")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    inputs = {
        "when": args.when,
        "where": args.where,
        "area_m2": args.m2,
        "rooms": args.rooms,
        "category": args.cat,
    }
    if args.bathrooms is not None:
        inputs["bathrooms"] = args.bathrooms
    if args.urgency:
        inputs["urgency"] = args.urgency
    if args.condition:
        inputs["condition"] = args.condition
    if args.addons:
        inputs["addons"] = args.addons

    result = resolve.resolve(inputs, check_calendar=not args.no_calendar)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.create_event:
        slots = result.get("calendar", {}).get("availableSlots") or []
        if not slots:
            print("\n[create-event] no free slots available, skipping.", file=sys.stderr)
            return 0
        from local import calendar_client
        first = slots[0]
        start = datetime.fromisoformat(first["start"])
        end = datetime.fromisoformat(first["end"])
        cal_id = result["calendar"]["calendarId"]
        alloc = result["allocation"]
        summary = f"{alloc['category']} – {alloc['personnel']['count']}p – {alloc['crew']['name']}"
        description = (
            f"Atrium auto-booking\n"
            f"Category: {alloc['category']}\n"
            f"Personnel: {alloc['personnel']['count']}\n"
            f"Vehicle: {alloc['vehicle']['label']}\n"
            f"Equipment: {', '.join(e['label'] for e in alloc['equipment'])}\n"
            f"Total: {result['pricing']['total']} {result['pricing']['currency']}"
        )
        created = calendar_client.create_event(
            calendar_id=cal_id, summary=summary,
            start=start, end=end,
            description=description, location=inputs["where"],
        )
        print(f"\n[create-event] {created}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
