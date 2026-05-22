"""Phase 1 slot extraction.

Two paths:

1. **Claude Converse** — preferred when `BedrockClaudeClient.is_available()`
   reports the runtime is wired. The handler hands each finalized caller turn
   to Claude with the 6-slot tool schema; Claude responds with `save_slot`
   tool calls.
2. **Deterministic local fallback** — regex/keyword scan over the turn text
   for the obvious demo utterances. Used when Bedrock is not configured (CI,
   `npm run build`, scripts/simulate_call_events.py). Good enough to produce
   at least three slot extractions for the demo script.

Both paths return a list of `(slot, value)` pairs that the handler turns into
`SlotSaved` events and routes through the slot adapter.
"""
from __future__ import annotations

import re
from typing import Iterable

from slot_state import REQUIRED_SLOTS, SlotState

SlotPair = tuple[str, object]


SERVICE_KEYWORDS: list[tuple[str, str]] = [
    ("move out", "MOVE_OUT_CLEANING"),
    ("move-out", "MOVE_OUT_CLEANING"),
    ("moveout", "MOVE_OUT_CLEANING"),
    ("end of tenancy", "MOVE_OUT_CLEANING"),
    ("office", "OFFICE_CLEANING"),
    ("construction", "CONSTRUCTION_CLEANING"),
    ("after build", "CONSTRUCTION_CLEANING"),
    ("post build", "CONSTRUCTION_CLEANING"),
    ("window", "WINDOW_CLEANING"),
    ("facility", "FACILITY_MAINTENANCE"),
    ("maintenance", "FACILITY_MAINTENANCE"),
]

URGENCY_KEYWORDS: list[tuple[str, str]] = [
    ("urgent", "urgent"),
    ("asap", "urgent"),
    ("right away", "urgent"),
    ("emergency", "urgent"),
    ("today", "urgent"),
    ("tomorrow", "high"),
    ("this week", "high"),
    ("next week", "medium"),
    ("no rush", "low"),
    ("whenever", "low"),
    ("flexible", "low"),
]

WHEN_KEYWORDS = [
    "today",
    "tomorrow",
    "this week",
    "next week",
    "this weekend",
    "next weekend",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
AREA_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:m2|m²|sqm|square\s*meters?|sq\s*ft|sqft|square\s*feet)",
    re.IGNORECASE,
)
ROOMS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:rooms?|bedrooms?|bdr|brs?)\b",
    re.IGNORECASE,
)

# Lex transcribes spoken numbers as words ("four rooms"); cover one to ten.
WORD_NUMBERS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
WORD_ROOMS_RE = re.compile(
    r"\b(" + "|".join(WORD_NUMBERS) + r")\s+(?:rooms?|bedrooms?)\b",
    re.IGNORECASE,
)


def extract_slots_deterministic(text: str, state: SlotState | None = None) -> list[SlotPair]:
    """Pull as many slots out of a single utterance as we can without Claude.

    Only emits slots that are *not* already filled in `state` to avoid duplicate
    `SlotSaved` events for the same value.
    """
    state = state or SlotState()
    found: list[SlotPair] = []
    lowered = text.lower()

    if not state.what:
        for needle, label in SERVICE_KEYWORDS:
            if needle in lowered:
                found.append(("what", label))
                break

    if not state.when:
        for needle in WHEN_KEYWORDS:
            if needle in lowered:
                found.append(("when", needle))
                break

    if state.area is None:
        match = AREA_RE.search(text)
        if match:
            try:
                area_value = float(match.group(1))
                unit = match.group(0).split(match.group(1), 1)[-1].strip().lower()
                found.append(("area", _format_area(area_value, unit)))
            except ValueError:
                pass

    if state.rooms is None:
        match = ROOMS_RE.search(text)
        if match:
            try:
                rooms_value = float(match.group(1))
                rooms_int = int(rooms_value) if rooms_value.is_integer() else rooms_value
                found.append(("rooms", rooms_int))
            except ValueError:
                pass
        else:
            word_match = WORD_ROOMS_RE.search(text)
            if word_match:
                found.append(("rooms", WORD_NUMBERS[word_match.group(1).lower()]))

    if not state.urgency:
        for needle, label in URGENCY_KEYWORDS:
            if needle in lowered:
                found.append(("urgency", label))
                break

    if not state.email:
        match = EMAIL_RE.search(text)
        if match:
            found.append(("email", match.group(0)))

    return found


def _format_area(value: float, unit: str) -> str:
    if "ft" in unit or "feet" in unit:
        return f"{value:g} sqft"
    return f"{value:g} m2"


def apply_extractions(state: SlotState, pairs: Iterable[SlotPair]) -> list[SlotPair]:
    """Apply each (slot, value) to `state` and return the kept pairs."""
    accepted: list[SlotPair] = []
    for slot, value in pairs:
        if slot not in REQUIRED_SLOTS:
            continue
        state.update(slot, value)
        accepted.append((slot, value))
    return accepted
