"""DynamoDB access helpers for the Input Agent.

These helpers are the durable side of the Phase 1 spine — every Lex turn,
every Connect contact-flow invocation, and the stream_to_appsync fan-out path
all read/write through this module. Tables are defined in
`infrastructure/stacks/data_stack.py`:

  Calls       PK=callId, SK=sk        ("meta" or "turn#<seq>"), stream on
  Bookings    PK=bookingId, SK=sk     ("current"), stream on
  Companies   PK=companyId
  Crews       PK=companyId, SK=crewId
  PriceMatrix PK=companyId, SK=serviceType
  KnowledgeItems PK=companyId, SK=itemId

Decimal-friendly: floats are stored as Decimal so DynamoDB doesn't reject them.
"""
from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key


def _ddb_resource():
    return boto3.resource("dynamodb")


def _table(env_name: str, fallback: str):
    return _ddb_resource().Table(os.environ.get(env_name, fallback))


def calls_table():
    return _table("CALLS_TABLE", "atrium-calls")


def bookings_table():
    return _table("BOOKINGS_TABLE", "atrium-bookings")


def companies_table():
    return _table("COMPANIES_TABLE", "atrium-companies")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _to_ddb(value: Any) -> Any:
    """Recursively coerce floats to Decimal for DynamoDB."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_ddb(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Call lifecycle
# ---------------------------------------------------------------------------


def start_call(
    call_id: str,
    booking_id: str,
    company_id: str,
    *,
    contact_id: str | None = None,
    caller: str | None = None,
    locale: str | None = None,
    company_name: str | None = None,
) -> dict[str, Any]:
    """Initialize Calls#meta + Bookings#current.

    Idempotent: if a Calls#meta row already exists we leave it alone (a Lex
    fulfillment may invoke this on every turn before checking).
    """
    now = _now_iso()
    calls_table().update_item(
        Key={"callId": call_id, "sk": "meta"},
        UpdateExpression=(
            "SET companyId = if_not_exists(companyId, :company_id), "
            "bookingId = if_not_exists(bookingId, :booking_id), "
            "contactId = if_not_exists(contactId, :contact_id), "
            "caller = if_not_exists(caller, :caller), "
            "locale = if_not_exists(locale, :locale), "
            "companyName = if_not_exists(companyName, :company_name), "
            "#status = if_not_exists(#status, :status), "
            "startedAt = if_not_exists(startedAt, :now), "
            "updatedAt = :now, "
            "turnSeq = if_not_exists(turnSeq, :zero)"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":company_id": company_id,
            ":booking_id": booking_id,
            ":contact_id": contact_id or "",
            ":caller": caller or "",
            ":locale": locale or "",
            ":company_name": company_name or "",
            ":status": "Live",
            ":now": now,
            ":zero": 0,
        },
    )
    bookings_table().update_item(
        Key={"bookingId": booking_id, "sk": "current"},
        UpdateExpression=(
            "SET callId = if_not_exists(callId, :call_id), "
            "companyId = if_not_exists(companyId, :company_id), "
            "slots = if_not_exists(slots, :empty), "
            "brain = if_not_exists(brain, :empty), "
            "#status = if_not_exists(#status, :status), "
            "updatedAt = :now"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":call_id": call_id,
            ":company_id": company_id,
            ":empty": {},
            ":status": "CALL_STARTED",
            ":now": now,
        },
    )
    return {
        "callId": call_id,
        "bookingId": booking_id,
        "companyId": company_id,
        "status": "Live",
        "startedAt": now,
    }


def get_call_meta(call_id: str) -> dict[str, Any]:
    response = calls_table().get_item(Key={"callId": call_id, "sk": "meta"})
    return response.get("Item", {})


def append_turn(
    call_id: str,
    seq: int,
    speaker: str,
    text: str,
    citations: list[dict] | None = None,
    company_id: str | None = None,
) -> dict[str, Any]:
    """Append a transcript turn. `company_id` is denormalized onto every turn
    record so the stream fan-out can route Wall events without an extra
    Calls#meta lookup."""
    now = _now_iso()
    if company_id is None:
        # Lookup meta once if caller didn't pass companyId in.
        meta = get_call_meta(call_id)
        company_id = meta.get("companyId", "")
    item = {
        "callId": call_id,
        "sk": f"turn#{seq:06d}",
        "companyId": company_id or "",
        "speaker": speaker,
        "transcriptChunk": text,
        "citations": _to_ddb(citations or []),
        "createdAt": now,
    }
    calls_table().put_item(Item=item)
    calls_table().update_item(
        Key={"callId": call_id, "sk": "meta"},
        UpdateExpression="SET turnSeq = :seq, updatedAt = :now",
        ExpressionAttributeValues={":seq": seq, ":now": now},
    )
    return item


def save_slot(call_id: str, booking_id: str, slot: str, value: Any) -> dict[str, Any]:
    now = _now_iso()
    bookings_table().update_item(
        Key={"bookingId": booking_id, "sk": "current"},
        UpdateExpression="SET slots = if_not_exists(slots, :empty), updatedAt = :now",
        ExpressionAttributeValues={":empty": {}, ":now": now},
    )
    bookings_table().update_item(
        Key={"bookingId": booking_id, "sk": "current"},
        UpdateExpression="SET slots.#slot = :value, updatedAt = :now",
        ExpressionAttributeNames={"#slot": slot},
        ExpressionAttributeValues={":value": _to_ddb(value), ":now": now},
    )
    return {
        "callId": call_id,
        "bookingId": booking_id,
        "slot": slot,
        "value": value,
        "status": "saved",
    }


def save_brain(booking_id: str, brain: dict[str, Any]) -> dict[str, Any]:
    now = _now_iso()
    bookings_table().update_item(
        Key={"bookingId": booking_id, "sk": "current"},
        UpdateExpression="SET brain = :brain, #status = :status, updatedAt = :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":brain": _to_ddb(brain),
            ":status": "ESTIMATED",
            ":now": now,
        },
    )
    return brain


def get_booking(booking_id: str) -> dict[str, Any]:
    response = bookings_table().get_item(Key={"bookingId": booking_id, "sk": "current"})
    return response.get("Item", {})


def end_call(call_id: str, booking_id: str, reason: str = "completed") -> dict[str, Any]:
    now = _now_iso()
    calls_table().update_item(
        Key={"callId": call_id, "sk": "meta"},
        UpdateExpression="SET #status = :status, endedAt = :now, endedReason = :reason, updatedAt = :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": "Ended", ":now": now, ":reason": reason},
    )
    if booking_id:
        bookings_table().update_item(
            Key={"bookingId": booking_id, "sk": "current"},
            UpdateExpression="SET #status = :status, updatedAt = :now",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": "BOOKING_CREATED", ":now": now},
        )
    return {"callId": call_id, "bookingId": booking_id, "reason": reason}


# ---------------------------------------------------------------------------
# Company / tenant lookup
# ---------------------------------------------------------------------------


def get_company(company_id: str) -> dict[str, Any]:
    response = companies_table().get_item(Key={"companyId": company_id})
    return response.get("Item", {})


def get_company_by_phone_number(phone_number: str) -> dict[str, Any]:
    """Look up the tenant a Connect-dialed phone number belongs to.

    No GSI exists for this; a small `Companies` table makes a scan acceptable
    for the demo. If/when we onboard many tenants, add a `phoneNumber` GSI.
    """
    if not phone_number:
        return {}
    scan = companies_table().scan(
        FilterExpression="phoneNumber = :p",
        ExpressionAttributeValues={":p": phone_number},
        Limit=1,
    )
    items = scan.get("Items", [])
    return items[0] if items else {}
