"""Shared wall-event contract.

Mirror of `web/src/lib/types.ts` so the Input Agent Lambda, wall fan-out path,
and frontend all speak the same JSON. Every event has `type`, `callId`,
`companyId`, and `timestamp`; the rest is event-specific.

Phase 1 only emits events for the live-call spine (call lifecycle, transcript,
slots, citations). Future phases append new `type` values; consumers must
treat unknown types as no-ops.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

EventType = Literal[
    "CallStarted",
    "TranscriptTurn",
    "SlotSaved",
    "CitationAdded",
    "CallEnded",
    "AgentSpeakingStart",
    "AgentSpeakingEnd",
    "Error",
    "BrainEstimate",
]


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _base(call_id: str, company_id: str, *, timestamp: str | None = None) -> dict[str, Any]:
    return {
        "callId": call_id,
        "companyId": company_id,
        "timestamp": timestamp or _now_iso(),
    }


def call_started(
    call_id: str,
    company_id: str,
    *,
    company_name: str | None = None,
    caller: str | None = None,
    locale: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    event = {"type": "CallStarted", **_base(call_id, company_id, timestamp=timestamp)}
    if company_name is not None:
        event["companyName"] = company_name
    if caller is not None:
        event["caller"] = caller
    if locale is not None:
        event["locale"] = locale
    return event


def transcript_turn(
    call_id: str,
    company_id: str,
    *,
    seq: int,
    speaker: Literal["Caller", "Agent"],
    text: str,
    is_final: bool = True,
    timestamp: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "TranscriptTurn",
        **_base(call_id, company_id, timestamp=timestamp),
        "seq": seq,
        "speaker": speaker,
        "text": text,
        "isFinal": is_final,
    }


def slot_saved(
    call_id: str,
    company_id: str,
    *,
    slot: str,
    value: Any,
    booking_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    event = {
        "type": "SlotSaved",
        **_base(call_id, company_id, timestamp=timestamp),
        "slot": slot,
        "value": "" if value is None else str(value),
    }
    if booking_id is not None:
        event["bookingId"] = booking_id
    return event


def citation_added(
    call_id: str,
    company_id: str,
    *,
    source: str,
    excerpt: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "CitationAdded",
        **_base(call_id, company_id, timestamp=timestamp),
        "source": source,
        "excerpt": excerpt,
    }


def call_ended(
    call_id: str,
    company_id: str,
    *,
    reason: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    event = {"type": "CallEnded", **_base(call_id, company_id, timestamp=timestamp)}
    if reason is not None:
        event["reason"] = reason
    return event


def agent_speaking_start(call_id: str, company_id: str, *, timestamp: str | None = None) -> dict[str, Any]:
    return {"type": "AgentSpeakingStart", **_base(call_id, company_id, timestamp=timestamp)}


def agent_speaking_end(call_id: str, company_id: str, *, timestamp: str | None = None) -> dict[str, Any]:
    return {"type": "AgentSpeakingEnd", **_base(call_id, company_id, timestamp=timestamp)}


def error(call_id: str, company_id: str, *, message: str, timestamp: str | None = None) -> dict[str, Any]:
    return {
        "type": "Error",
        **_base(call_id, company_id, timestamp=timestamp),
        "message": message,
    }


def brain_estimate(
    call_id: str,
    company_id: str,
    *,
    service_type: str,
    price: float,
    currency: str,
    needs_photos: bool = False,
    timestamp: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "BrainEstimate",
        **_base(call_id, company_id, timestamp=timestamp),
        "serviceType": service_type,
        "price": price,
        "currency": currency,
        "needsPhotos": needs_photos,
    }


@dataclass
class EventSink:
    """Collect events for unit tests and local simulation.

    Production code paths pass a callable (e.g. a closure over
    apigatewaymanagementapi.post_to_connection); tests use this sink so they
    can assert the exact sequence of emitted events.
    """

    events: list[dict[str, Any]]

    def __init__(self) -> None:
        self.events = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)
