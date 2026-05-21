"""Publish DynamoDB stream records to the Live Call Wall.

The AppSync mutation call is left as a small integration point; the event
normalization is stable so frontend work can proceed with matching mock events.
"""
from __future__ import annotations

from typing import Any


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    keys = record.get("dynamodb", {}).get("Keys", {})
    return {
        "eventName": record.get("eventName"),
        "callId": keys.get("callId", {}).get("S"),
        "bookingId": keys.get("bookingId", {}).get("S"),
        "raw": record,
    }


def handler(event, context):
    events = [normalize_record(record) for record in event.get("Records", [])]
    return {"published": len(events), "events": events}
