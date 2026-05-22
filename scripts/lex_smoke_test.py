"""Local smoke test: replay a sequence of Lex V2 CodeHook events through the Lambda.

Hits real Bedrock + real DynamoDB (the same way the deployed Lambda would). Use
this to verify the turn loop + tools work without paying for an actual phone call.

Run:
    AWS_PROFILE=... python scripts/lex_smoke_test.py
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambdas" / "input_agent"))

from handler import lambda_handler  # noqa: E402


def make_event(session_id: str, transcript: str, attrs: dict) -> dict:
    return {
        "sessionId": session_id,
        "inputTranscript": transcript,
        "inputMode": "Speech",
        "bot": {"id": "test", "aliasId": "test", "version": "DRAFT", "name": "atrium-receptionist", "localeId": "en_US"},
        "interpretations": [{"intent": {"name": "FallbackIntent"}}],
        "sessionState": {
            "sessionAttributes": attrs,
            "intent": {"name": "FallbackIntent", "state": "InProgress"},
        },
    }


def main() -> int:
    session_id = f"smoke_{uuid.uuid4().hex[:8]}"
    attrs: dict[str, str] = {"companyId": "glanz-ag"}

    utterances = [
        "Hi, I need a move-out cleaning",
        "It is around eighty square metres, two bedrooms",
        "Next Friday morning would work",
        "Urgency is low, the address is Bahnhofstrasse twelve, eight thousand one Zurich",
    ]

    for i, text in enumerate(utterances, 1):
        event = make_event(session_id, text, attrs)
        print(f"\n--- TURN {i}: caller -> {text!r}")
        response = lambda_handler(event, None)
        spoken = response["messages"][0]["content"]
        print(f"    agent -> {spoken!r}")
        new_attrs = response["sessionState"]["sessionAttributes"]
        # keep messages out of the printed payload (too noisy)
        printable = {k: v for k, v in new_attrs.items() if k != "messages"}
        print(f"    attrs -> {json.dumps(printable)}")
        attrs = new_attrs
        if response["sessionState"]["dialogAction"]["type"] == "Close":
            print("    [call ended]")
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
