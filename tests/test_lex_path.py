from __future__ import annotations

import os

import slot_adapter
from handler import handle_lex_event
from session import reset_all


def setup_function(_func):
    reset_all()
    slot_adapter.reset()
    os.environ.pop("DDB_BACKEND", None)


def _lex_event(
    transcript: str,
    attrs: dict[str, str] | None = None,
    slots: dict | None = None,
) -> dict:
    return {
        "sessionState": {
            "sessionAttributes": dict(attrs or {}),
            "intent": {
                "name": "CollectBooking",
                "slots": dict(slots or {}),
                "state": "InProgress",
            },
        },
        "inputTranscript": transcript,
        "invocationSource": "DialogCodeHook",
        "bot": {"id": "bot-id", "name": "atrium-input-agent"},
    }


def test_lex_first_turn_elicits_remaining_slot():
    response = handle_lex_event(
        _lex_event(
            "I need a move-out cleaning tomorrow for 85 square meters, urgent, customer@example.com",
        ),
    )
    state = response["sessionState"]
    assert state["dialogAction"]["type"] == "ElicitSlot"
    # 5 slots extracted (when, what, area, urgency, email) — only `rooms` remains.
    assert state["dialogAction"]["slotToElicit"] == "rooms"
    assert state["sessionAttributes"]["callId"].startswith("call-")
    assert state["sessionAttributes"]["bookingId"].startswith("booking-")
    # Lex receives a plain-text prompt to read back to the caller.
    assert response["messages"][0]["content"]


def test_lex_completes_when_all_slots_filled():
    attrs: dict[str, str] = {}
    slots: dict = {}
    transcripts = [
        "I need a move-out cleaning tomorrow for 85 m2, urgent, customer@example.com",
        "4 rooms",
    ]
    for transcript in transcripts:
        response = handle_lex_event(_lex_event(transcript, attrs, slots))
        attrs = response["sessionState"]["sessionAttributes"]
        slots = response["sessionState"]["intent"].get("slots") or {}
    state = response["sessionState"]
    assert state["dialogAction"]["type"] == "Close"
    assert state["intent"]["state"] == "Fulfilled"


def test_lex_keeps_session_attributes_stable_across_turns():
    response_a = handle_lex_event(_lex_event("move out tomorrow"))
    attrs = response_a["sessionState"]["sessionAttributes"]
    response_b = handle_lex_event(_lex_event("85 m2", attrs))
    assert response_b["sessionState"]["sessionAttributes"]["callId"] == attrs["callId"]
    assert (
        response_b["sessionState"]["sessionAttributes"]["bookingId"]
        == attrs["bookingId"]
    )
