from __future__ import annotations

from slot_extraction import apply_extractions, extract_slots_deterministic
from slot_state import SlotState


def test_extract_what_when_area_location():
    state = SlotState()
    pairs = extract_slots_deterministic(
        "I need a move-out cleaning tomorrow for 85 m2 at Bahnhofstrasse 12, 8001 Zürich.",
        state,
    )
    accepted = dict(apply_extractions(state, pairs))
    assert accepted["what"] == "MOVE_OUT_CLEANING"
    assert accepted["when"] == "tomorrow"
    assert "m2" in str(accepted["area"]).lower()
    assert accepted["location"] == "Bahnhofstrasse 12, 8001 Zürich"


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


def test_swiss_address_basic():
    state = SlotState()
    pairs = extract_slots_deterministic(
        "The address is Bahnhofstrasse 12, 8001 Zürich.",
        state,
    )
    apply_extractions(state, pairs)
    assert state.location == "Bahnhofstrasse 12, 8001 Zürich"


def test_swiss_address_no_comma():
    state = SlotState()
    pairs = extract_slots_deterministic("I live at Limmatquai 50 8001 Zurich.", state)
    apply_extractions(state, pairs)
    assert state.location == "Limmatquai 50, 8001 Zurich"


def test_swiss_address_rejects_invalid_plz():
    """PLZ must be 1000–9999; '0123' or '99' are not Swiss postal codes."""
    state = SlotState()
    pairs = extract_slots_deterministic("Somewhere Street 1, 0123 Nowhere.", state)
    apply_extractions(state, pairs)
    assert state.location is None


def test_required_slots_progress():
    state = SlotState()
    pairs = extract_slots_deterministic(
        "Move-out cleaning tomorrow, 85 m2, 4 rooms, urgent, Bahnhofstrasse 12, 8001 Zürich.",
        state,
    )
    apply_extractions(state, pairs)
    missing = state.missing()
    # 5 slots from this single utterance — only `when`/`what`/etc. may remain.
    assert len(missing) <= 1
