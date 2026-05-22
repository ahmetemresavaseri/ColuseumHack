from __future__ import annotations

from slot_extraction import apply_extractions, extract_slots_deterministic
from slot_state import SlotState


def test_extract_what_when_area():
    state = SlotState()
    pairs = extract_slots_deterministic(
        "I need a move-out cleaning tomorrow for 85 m2",
        state,
    )
    accepted = dict(apply_extractions(state, pairs))
    assert accepted["what"] == "MOVE_OUT_CLEANING"
    assert accepted["when"] == "tomorrow"
    assert "m2" in str(accepted["area"]).lower()


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


def test_word_number_area():
    state = SlotState()
    pairs = extract_slots_deterministic("fifty square meters", state)
    apply_extractions(state, pairs)
    assert "50" in str(state.area)


def test_word_number_rooms_with_keyword():
    state = SlotState()
    pairs = extract_slots_deterministic("twenty rooms", state)
    apply_extractions(state, pairs)
    assert state.rooms == 20


def test_bare_word_number_no_longer_auto_rooms():
    """A bare 'three' is context-dependent (could be area OR rooms). The
    transcript extractor must NOT auto-assign it — the handler resolves bare
    numbers from Lex's slot-elicitation context instead.
    """
    state = SlotState(when="tomorrow", what="OFFICE_CLEANING", area=85, urgency="high")
    pairs = extract_slots_deterministic("three.", state)
    apply_extractions(state, pairs)
    assert state.rooms is None


def test_required_slots_progress():
    state = SlotState()
    pairs = extract_slots_deterministic(
        "Move-out cleaning tomorrow, 85 m2, 4 rooms, urgent",
        state,
    )
    apply_extractions(state, pairs)
    missing = state.missing()
    # 4 slots extracted from one utterance — only `location` left to elicit.
    assert missing == ["location"]
