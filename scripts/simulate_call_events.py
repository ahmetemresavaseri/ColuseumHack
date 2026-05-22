"""Emit a Phase 1 call event stream matching the shared wall event contract.

Usage:
  python scripts/simulate_call_events.py
  python scripts/simulate_call_events.py --pretty

The events emitted match `web/src/lib/types.ts` `WallEvent` and the Python
helpers in `lambdas/input_agent/events.py`. Pipe the output anywhere that
expects the same JSON shape the Lambda fan-out would post.

This script does NOT read or write DynamoDB. It exercises the deterministic
text-turn path through the Input Agent handler so a teammate can see slots,
citations, and a brain estimate appearing on the wall before any real AWS
wiring exists.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambdas" / "input_agent"))

import events as wall_events  # noqa: E402
from handler import (  # noqa: E402
    handle_agent_say,
    handle_end_call,
    handle_start_call,
    handle_text_turn,
    make_local_session,
)


CALLER_TURNS: list[tuple[str, str]] = [
    ("Agent", "Hello, Glanz AG. How can I help?"),
    ("Caller", "I need a move-out cleaning tomorrow for 85 square meters."),
    ("Agent", "Got it. How many rooms?"),
    ("Caller", "4 rooms, it's urgent. The address is Bahnhofstrasse 12, 8001 Zürich."),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pretty", action="store_true", help="Indent output JSON.")
    parser.add_argument("--company", default="glanz-ag")
    args = parser.parse_args(argv)

    session, sink = make_local_session(company_id=args.company)

    handle_start_call(
        session,
        sink,
        company_name="Glanz AG",
        caller="+41 44 000 00 00",
        locale="de-CH",
    )

    for speaker, text in CALLER_TURNS:
        if speaker == "Agent":
            handle_agent_say(session, text, sink)
        else:
            handle_text_turn(session, text, sink)

    sink(
        wall_events.citation_added(
            session.call_id,
            session.company_id,
            source="Pricelist.pdf p.3",
            excerpt="Move-out cleaning starts with a base fee and area rate.",
        )
    )
    sink(
        wall_events.brain_estimate(
            session.call_id,
            session.company_id,
            service_type="MOVE_OUT_CLEANING",
            price=703.13,
            currency="CHF",
            needs_photos=False,
        )
    )

    handle_end_call(session, sink, reason="completed")

    indent = 2 if args.pretty else None
    for event in sink.events:
        print(json.dumps(event, indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
