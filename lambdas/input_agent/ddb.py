"""DynamoDB access helpers for the Input Agent."""
from __future__ import annotations

import os
from typing import Any

import boto3


def _ddb_resource():
    return boto3.resource("dynamodb")


def table(name_env: str, fallback: str):
    return _ddb_resource().Table(os.environ.get(name_env, fallback))


def save_slot(call_id: str, booking_id: str, slot: str, value: Any) -> dict[str, Any]:
    bookings = table("BOOKINGS_TABLE", "atrium-bookings")
    bookings.update_item(
        Key={"bookingId": booking_id, "sk": "current"},
        UpdateExpression="SET slots = if_not_exists(slots, :empty)",
        ExpressionAttributeValues={":empty": {}},
    )
    bookings.update_item(
        Key={"bookingId": booking_id, "sk": "current"},
        UpdateExpression="SET slots.#slot = :value",
        ExpressionAttributeNames={"#slot": slot},
        ExpressionAttributeValues={":value": value},
    )
    return {"callId": call_id, "bookingId": booking_id, "slot": slot, "value": value}


def append_turn(call_id: str, seq: int, speaker: str, text: str, citations: list[dict] | None = None) -> None:
    calls = table("CALLS_TABLE", "atrium-calls")
    calls.put_item(
        Item={
            "callId": call_id,
            "sk": f"turn#{seq:06d}",
            "speaker": speaker,
            "transcriptChunk": text,
            "citations": citations or [],
        }
    )


def get_company(company_id: str) -> dict[str, Any]:
    companies = table("COMPANIES_TABLE", "atrium-companies")
    response = companies.get_item(Key={"companyId": company_id})
    return response.get("Item", {})
