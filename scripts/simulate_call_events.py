"""Emit sample call events matching the Live Call Wall contract."""
from __future__ import annotations

import json


EVENTS = [
    {"type": "CallStarted", "callId": "demo-call-001", "companyId": "glanz-ag"},
    {"type": "SlotSaved", "callId": "demo-call-001", "slot": "what", "value": "MOVE_OUT_CLEANING"},
    {"type": "BrainEstimate", "callId": "demo-call-001", "price": 656.25, "currency": "CHF"},
]


def main() -> int:
    for event in EVENTS:
        print(json.dumps(event))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
