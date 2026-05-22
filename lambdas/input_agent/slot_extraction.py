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

# Lex transcribes spoken numbers as words ("four rooms"); cover one to ninety-nine.
WORD_NUMBERS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}
_WORD_NUM_PATTERN = "|".join(sorted(WORD_NUMBERS, key=len, reverse=True))
WORD_ROOMS_RE = re.compile(
    rf"\b({_WORD_NUM_PATTERN})\s+(?:rooms?|bedrooms?)\b",
    re.IGNORECASE,
)
# Match phrases like "fifty square meters" or "forty sqft".
WORD_AREA_RE = re.compile(
    rf"\b((?:{_WORD_NUM_PATTERN})(?:[\s-]+(?:{_WORD_NUM_PATTERN}))*)\s*"
    r"(m2|m²|sqm|square\s*meters?|sq\s*ft|sqft|square\s*feet)",
    re.IGNORECASE,
)
# Standalone word-number (used when Lex prompt was "rooms" and the caller
# just said "three").
WORD_NUMBER_ONLY_RE = re.compile(
    rf"^\s*((?:{_WORD_NUM_PATTERN})(?:[\s-]+(?:{_WORD_NUM_PATTERN}))*)\s*\.?\s*$",
    re.IGNORECASE,
)


def _parse_word_number(text: str) -> int | None:
    """Convert 'forty', 'twenty five', 'one hundred' → int. Best-effort."""
    total = 0
    chunk = 0
    found_any = False
    for tok in re.split(r"[\s-]+", text.strip().lower()):
        if tok in WORD_NUMBERS:
            n = WORD_NUMBERS[tok]
            found_any = True
            if n == 100:
                chunk = max(chunk, 1) * 100
            else:
                chunk += n
        elif tok in {"and", ""}:
            continue
        else:
            return None
    total += chunk
    return total if found_any else None


# Voice-spelled emails: "a. b. c. at gmail dot com" → "abc@gmail.com".
# Captures repeated 1-2 char tokens (a, b, c) before "at", then "<word> dot
# <word>" after. Best-effort; not robust against arbitrary spelling.
VOICE_EMAIL_RE = re.compile(
    r"\b([A-Za-z](?:\.\s*[A-Za-z])+)\s*\.?\s*(?:at|@)\s*([A-Za-z0-9]+)"
    r"\s*(?:dot|\.)\s*([A-Za-z]{2,})\b",
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
        else:
            word_match = WORD_AREA_RE.search(text)
            if word_match:
                num = _parse_word_number(word_match.group(1))
                if num is not None:
                    found.append(("area", _format_area(float(num), word_match.group(2).lower())))

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
                num = _parse_word_number(word_match.group(1))
                if num is not None:
                    found.append(("rooms", num))

    if not state.urgency:
        for needle, label in URGENCY_KEYWORDS:
            if needle in lowered:
                found.append(("urgency", label))
                break

    if not state.email:
        match = EMAIL_RE.search(text)
        if match:
            found.append(("email", match.group(0)))
        else:
            voice = VOICE_EMAIL_RE.search(text)
            if voice:
                local = re.sub(r"[\s.]", "", voice.group(1)).lower()
                domain = voice.group(2).lower()
                tld = voice.group(3).lower()
                found.append(("email", f"{local}@{domain}.{tld}"))

    # Lex prompts each slot one at a time; if the caller's whole utterance is
    # just a word-number (e.g. "three" answering "How many rooms?"), accept it
    # as the next missing slot.
    if state.rooms is None and not any(s == "rooms" for s, _ in found):
        bare = WORD_NUMBER_ONLY_RE.match(text)
        if bare:
            num = _parse_word_number(bare.group(1))
            if num is not None:
                found.append(("rooms", num))

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
