from __future__ import annotations

import events as wall_events
import slot_adapter
from handler import (
    handle_end_call,
    handle_start_call,
    handle_text_turn,
)
from session import new_session, reset_all


def setup_function(_func):
    reset_all()
    slot_adapter.reset()


def test_text_turn_emits_transcript_and_slots():
    session = new_session(company_id="glanz-ag")
    sink = wall_events.EventSink()

    handle_start_call(session, sink, company_name="Glanz AG", locale="de-CH")
    handle_text_turn(
        session,
        "I need a move-out cleaning tomorrow for 85 m2, urgent, customer@example.com",
        sink,
    )
    handle_end_call(session, sink, reason="completed")

    types = [e["type"] for e in sink.events]
    assert types[0] == "CallStarted"
    assert "TranscriptTurn" in types
    slot_types = [e for e in sink.events if e["type"] == "SlotSaved"]
    # At least three slots out of the demo utterance.
    assert len(slot_types) >= 3
    saved_slots = {e["slot"] for e in slot_types}
    assert {"what", "when"}.issubset(saved_slots)
    assert types[-1] == "CallEnded"


def test_slot_adapter_returns_canonical_shape():
    result = slot_adapter.save_slot(
        call_id="c1", booking_id="b1", slot="location", value="Zurich Altstadt"
    )
    assert result == {
        "callId": "c1",
        "bookingId": "b1",
        "slot": "location",
        "value": "Zurich Altstadt",
        "status": "saved",
        "backend": "mock",
    }
    assert slot_adapter.snapshot("c1", "b1") == {"location": "Zurich Altstadt"}


def test_text_turn_does_not_re_emit_filled_slot():
    session = new_session(company_id="glanz-ag")
    sink = wall_events.EventSink()
    handle_start_call(session, sink)
    handle_text_turn(session, "move-out cleaning tomorrow", sink)
    sink.events.clear()
    # Same utterance again — nothing new should be saved.
    handle_text_turn(session, "move-out cleaning tomorrow", sink)
    new_slots = [e for e in sink.events if e["type"] == "SlotSaved"]
    assert new_slots == []
