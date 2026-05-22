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
    """End-to-end flow: collect, estimate, react, any-questions, close."""
    attrs: dict[str, str] = {}
    slots: dict = {}
    transcripts = [
        "I need a move-out cleaning tomorrow for 85 m2, urgent, customer@example.com",
        "4 rooms",   # fills last booking slot -> handler speaks estimate
        "okay",      # react_to_estimate -> email already filled, skips to any_questions
        "no thanks", # answer to "any questions?" -> close
    ]
    phases = []
    for transcript in transcripts:
        response = handle_lex_event(_lex_event(transcript, attrs, slots))
        attrs = response["sessionState"]["sessionAttributes"]
        slots = response["sessionState"]["intent"].get("slots") or {}
        phases.append(attrs.get("phase"))
    state = response["sessionState"]
    # First turn doesn't set phase explicitly (defaults to collecting); then
    # estimate_spoken after rooms fills, then any_questions twice (the
    # react-skip-to-questions path and the close response).
    assert phases == [None, "estimate_spoken", "any_questions", "any_questions"]
    assert state["dialogAction"]["type"] == "Close"
    assert state["intent"]["state"] == "Fulfilled"


def test_brain_skipped_when_not_configured(monkeypatch):
    """With DDB_BACKEND=mock the brain branch is gated out; the handler must
    not crash and must not stamp `brainComputed`."""
    monkeypatch.delenv("DDB_BACKEND", raising=False)
    monkeypatch.delenv("BRAIN_FUNCTION_NAME", raising=False)
    response = handle_lex_event(
        _lex_event(
            "I need a move-out cleaning tomorrow for 85 square meters, urgent, customer@example.com",
        ),
    )
    # 5 slots filled; next ElicitSlot is rooms. No brainComputed flag yet
    # because we're in mock-DDB mode (Brain only fires when USE_REAL_DDB is on).
    attrs = response["sessionState"]["sessionAttributes"]
    assert attrs.get("brainComputed") in (None, "")


def test_bare_number_lands_in_elicited_area_not_rooms():
    """Caller answers 'thirty' to "How many square meters?" — that should
    fill `area`, not be misassigned to `rooms` by the transcript extractor.
    """
    from handler import _bare_number_fallback
    # Reproduces: Lex captured area="thirty" because area was being elicited.
    assert _bare_number_fallback("area", "thirty") == "30 m2"
    assert _bare_number_fallback("rooms", "three") == 3
    # what stays None — service type needs canonical mapping.
    assert _bare_number_fallback("what", "five") is None


def test_bare_number_strips_leading_a():
    """ASR commonly prepends 'a ' before the real utterance."""
    from handler import _bare_number_fallback
    assert _bare_number_fallback("rooms", "a five") == 5
    assert _bare_number_fallback("rooms", "a 5") == 5
    assert _bare_number_fallback("area", "a fifty square meters") == "50 m2" or \
           _bare_number_fallback("area", "a fifty") == "50 m2"
    assert _bare_number_fallback("when", "a in three days") == "in three days"


def test_urgency_derived_from_when():
    from handler import _derive_urgency_from_when
    assert _derive_urgency_from_when("today") == "high"
    assert _derive_urgency_from_when("tomorrow") == "high"
    assert _derive_urgency_from_when("in three days") == "medium"
    assert _derive_urgency_from_when("in two days") == "high"
    assert _derive_urgency_from_when("next week") == "medium"
    assert _derive_urgency_from_when("this week") == "high"
    assert _derive_urgency_from_when("whenever") == "low"
    assert _derive_urgency_from_when("flexible") == "low"
    assert _derive_urgency_from_when("monday") == "medium"
    assert _derive_urgency_from_when("urgent") == "high"
    assert _derive_urgency_from_when("") is None


def test_looks_like_email_rejects_questions():
    from handler import _looks_like_email
    assert not _looks_like_email("i have another question")
    assert not _looks_like_email("no thanks")
    assert not _looks_like_email("")
    assert _looks_like_email("a@b.com")
    assert _looks_like_email("Customer.Name+demo@example.co.uk")


def test_when_fallback_accepts_free_text():
    """'in three days', 'next monday morning' — calendar phrases don't match
    our keyword list but must still flow through to Bookings.slots."""
    from handler import _bare_number_fallback
    assert _bare_number_fallback("when", "in three days") == "in three days"
    assert _bare_number_fallback("when", "next monday morning") == "next monday morning"
    # Sentence-length text is rejected (probably a missed-question FAQ).
    long_text = "x " * 50
    assert _bare_number_fallback("when", long_text) is None


def test_lex_keeps_session_attributes_stable_across_turns():
    response_a = handle_lex_event(_lex_event("move out tomorrow"))
    attrs = response_a["sessionState"]["sessionAttributes"]
    response_b = handle_lex_event(_lex_event("85 m2", attrs))
    assert response_b["sessionState"]["sessionAttributes"]["callId"] == attrs["callId"]
    assert (
        response_b["sessionState"]["sessionAttributes"]["bookingId"]
        == attrs["bookingId"]
    )
