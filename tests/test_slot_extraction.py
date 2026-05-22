from __future__ import annotations

from slot_extraction import apply_extractions, extract_slots_deterministic
from slot_state import SlotState


def test_extract_what_when_area_email():
    state = SlotState()
    pairs = extract_slots_deterministic(
        "I need a move-out cleaning tomorrow for 85 m2, email customer@example.com",
        state,
    )
    accepted = dict(apply_extractions(state, pairs))
    assert accepted["what"] == "MOVE_OUT_CLEANING"
    assert accepted["when"] == "tomorrow"
    assert "m2" in str(accepted["area"]).lower()
    assert accepted["email"] == "customer@example.com"


def test_extract_rooms_and_urgency():
    state = SlotState()
    pairs = extract_slots_deterministic(
        "It's urgent, 4 bedrooms please",
        state,
    )
    accepted = dict(apply_extractions(state, pairs))
    assert accepted["rooms"] in (4, 4.0)
    assert accepted["urgency"] == "urgent"


def test_extraction_does_not_overwrite_existing_slots():
    state = SlotState(what="OFFICE_CLEANING")
    pairs = extract_slots_deterministic(
        "Actually a move-out clean would do",
        state,
    )
    # `what` already set — should not be re-extracted.
    assert ("what", "MOVE_OUT_CLEANING") not in pairs


def test_sqft_recognized():
    state = SlotState()
    pairs = extract_slots_deterministic("we have 900 sqft", state)
    accepted = dict(apply_extractions(state, pairs))
    assert "sqft" in str(accepted["area"]).lower()


def test_required_slots_progress():
    state = SlotState()
    pairs = extract_slots_deterministic(
        "Move-out cleaning tomorrow, 85 m2, 4 rooms, urgent, customer@example.com",
        state,
    )
    apply_extractions(state, pairs)
    missing = state.missing()
    # 5 slots from this single utterance — only `when`/`what`/etc. may remain.
    assert len(missing) <= 1
