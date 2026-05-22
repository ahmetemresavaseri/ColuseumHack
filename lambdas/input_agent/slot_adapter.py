"""Adapter boundary between slot extraction and durable storage.

DynamoDB writes are owned by another stream of work (see `ddb.py`); the
Phase 1 spine routes every "I want to save a slot" call through this adapter
so the frontend and voice loops can ship before the real tables exist.

When `DDB_BACKEND=real` is set in the environment, the adapter delegates to
`ddb.save_slot` (which the teammates implement against the real Bookings
table). Otherwise it uses a small in-memory dictionary keyed by
`(callId, bookingId)` so local simulation and tests still observe the same
return shape.
"""
from __future__ import annotations

import os
from typing import Any

try:
    from ddb import save_slot as _ddb_save_slot
except Exception:  # pragma: no cover - ddb module is mock-friendly today
    _ddb_save_slot = None  # type: ignore[assignment]


_IN_MEMORY: dict[tuple[str, str], dict[str, Any]] = {}


def _use_real_backend() -> bool:
    return os.environ.get("DDB_BACKEND", "mock").lower() == "real" and _ddb_save_slot is not None


def save_slot(call_id: str, booking_id: str, slot: str, value: Any) -> dict[str, Any]:
    """Save a slot through whichever backend is configured.

    Always returns the canonical shape `{ callId, bookingId, slot, value, status }`
    so callers (and the wall event emitter) don't need to know which backend
    answered.
    """
    if _use_real_backend():
        # TODO(teammates): owner of `ddb.py` wires the real DynamoDB write.
        result = _ddb_save_slot(call_id=call_id, booking_id=booking_id, slot=slot, value=value)  # type: ignore[misc]
        return {
            "callId": call_id,
            "bookingId": booking_id,
            "slot": slot,
            "value": value,
            "status": "saved",
            "backend": "ddb",
            **(result or {}),
        }

    key = (call_id, booking_id)
    bucket = _IN_MEMORY.setdefault(key, {})
    bucket[slot] = value
    return {
        "callId": call_id,
        "bookingId": booking_id,
        "slot": slot,
        "value": value,
        "status": "saved",
        "backend": "mock",
    }


def snapshot(call_id: str, booking_id: str) -> dict[str, Any]:
    """Return all slots persisted so far for this booking (mock backend only)."""
    return dict(_IN_MEMORY.get((call_id, booking_id), {}))


def reset() -> None:
    """Drop the in-memory store (used by tests)."""
    _IN_MEMORY.clear()
