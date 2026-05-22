"""Adapter boundary between slot extraction and durable storage.

`DDB_BACKEND=real`  → delegate to `ddb.save_slot` (default inside the deployed
                      Input Agent Lambda; `lambda_stack.py` sets this).
`DDB_BACKEND=mock`  → in-memory dictionary keyed by `(callId, bookingId)`.
                      Used by tests, local sims, and the Vite dev server.

Both backends return the canonical
  { callId, bookingId, slot, value, status, backend }
shape so the wall fan-out doesn't need to know which one answered.
"""
from __future__ import annotations

import logging
import os
from typing import Any

try:
    from ddb import save_slot as _ddb_save_slot
except Exception:  # pragma: no cover - import-time AWS credential failure
    _ddb_save_slot = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_IN_MEMORY: dict[tuple[str, str], dict[str, Any]] = {}


def _use_real_backend() -> bool:
    return (
        os.environ.get("DDB_BACKEND", "mock").lower() == "real"
        and _ddb_save_slot is not None
    )


def save_slot(call_id: str, booking_id: str, slot: str, value: Any) -> dict[str, Any]:
    if _use_real_backend():
        try:
            result = _ddb_save_slot(  # type: ignore[misc]
                call_id=call_id, booking_id=booking_id, slot=slot, value=value
            )
            return {
                "callId": call_id,
                "bookingId": booking_id,
                "slot": slot,
                "value": value,
                "status": "saved",
                "backend": "ddb",
                **(result or {}),
            }
        except Exception as exc:  # pragma: no cover - depends on AWS env
            logger.warning("ddb save_slot failed; falling back to mock: %s", exc)

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
    return dict(_IN_MEMORY.get((call_id, booking_id), {}))


def reset() -> None:
    _IN_MEMORY.clear()
