"""Publish DynamoDB stream records to AppSync as `WallEvent`s.

Consumes the `Calls` and `Bookings` streams configured in
`infrastructure/stacks/lambda_stack.py`, normalizes each record into the
shared wall event contract, and posts an IAM-signed GraphQL
`publishWallEvent` mutation to the AppSync API created by `api_stack.py`.

Event contract: identical to `lambdas/input_agent/events.py` and the
TypeScript `WallEvent` union in `web/src/lib/types.ts`.
"""
from __future__ import annotations

import json
import logging
import os
import time
from decimal import Decimal
from typing import Any, Iterable

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

logger = logging.getLogger()
logger.setLevel(logging.INFO)

APPSYNC_URL = os.environ.get("APPSYNC_GRAPHQL_URL", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

PUBLISH_MUTATION = """
mutation Publish($event: WallEventInput!) {
  publishWallEvent(event: $event) {
    callId
    companyId
    timestamp
    type
    payload
  }
}
""".strip()

# ---------------------------------------------------------------------------
# DDB stream record -> WallEvent normalization
# ---------------------------------------------------------------------------


def _new_image(record: dict[str, Any]) -> dict[str, Any]:
    return _from_ddb(record.get("dynamodb", {}).get("NewImage", {}) or {})


def _old_image(record: dict[str, Any]) -> dict[str, Any]:
    return _from_ddb(record.get("dynamodb", {}).get("OldImage", {}) or {})


def _from_ddb(image: dict[str, Any]) -> dict[str, Any]:
    """Translate a DDB AttributeValue map into a plain dict."""
    out: dict[str, Any] = {}
    for key, av in image.items():
        out[key] = _decode_attribute(av)
    return out


def _decode_attribute(av: Any) -> Any:
    if not isinstance(av, dict):
        return av
    if "S" in av:
        return av["S"]
    if "N" in av:
        try:
            return float(av["N"]) if "." in av["N"] else int(av["N"])
        except ValueError:
            return av["N"]
    if "BOOL" in av:
        return av["BOOL"]
    if "NULL" in av:
        return None
    if "M" in av:
        return {k: _decode_attribute(v) for k, v in av["M"].items()}
    if "L" in av:
        return [_decode_attribute(v) for v in av["L"]]
    if "SS" in av:
        return list(av["SS"])
    if "NS" in av:
        return [float(n) if "." in n else int(n) for n in av["NS"]]
    return av


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Return zero or more WallEvent dicts for a single DDB stream record."""
    event_name = record.get("eventName")
    if event_name not in {"INSERT", "MODIFY", "REMOVE"}:
        return []
    new_image = _new_image(record)
    old_image = _old_image(record)
    image = new_image or old_image
    if not image:
        return []

    # Discriminate by the sk value — Bookings always uses "current", Calls
    # uses "meta" or "turn#NNNNNN". Some records carry both bookingId and
    # callId attributes (Calls#meta links to a booking), so checking by PK
    # presence alone is ambiguous.
    sk = image.get("sk", "")
    if sk == "current" and "bookingId" in image:
        return _from_bookings(record, new_image, old_image, image)
    if (sk == "meta" or sk.startswith("turn#")) and "callId" in image:
        return _from_calls(record, new_image, old_image, image)
    return []


def _from_calls(
    record: dict[str, Any],
    new_image: dict[str, Any],
    old_image: dict[str, Any],
    image: dict[str, Any],
) -> list[dict[str, Any]]:
    sk = image.get("sk", "")
    call_id = image.get("callId", "")
    company_id = image.get("companyId", "")
    ts = image.get("updatedAt") or image.get("createdAt") or _now_iso()

    events: list[dict[str, Any]] = []

    if sk == "meta":
        old_status = old_image.get("status") if old_image else None
        new_status = new_image.get("status") if new_image else None
        if record.get("eventName") == "INSERT" and new_status == "Live":
            events.append(
                _wall_event(
                    "CallStarted",
                    call_id,
                    company_id,
                    ts,
                    {
                        "companyName": new_image.get("companyName"),
                        "caller": new_image.get("caller"),
                        "locale": new_image.get("locale"),
                    },
                )
            )
        elif old_status != new_status and new_status == "Ended":
            events.append(
                _wall_event(
                    "CallEnded",
                    call_id,
                    company_id,
                    ts,
                    {"reason": new_image.get("endedReason") or "completed"},
                )
            )
    elif sk.startswith("turn#") and record.get("eventName") == "INSERT":
        seq = _parse_seq(sk)
        speaker = image.get("speaker", "Caller")
        text = image.get("transcriptChunk", "")
        events.append(
            _wall_event(
                "TranscriptTurn",
                call_id,
                company_id,
                ts,
                {"seq": seq, "speaker": speaker, "text": text, "isFinal": True},
            )
        )
        for citation in image.get("citations") or []:
            events.append(
                _wall_event(
                    "CitationAdded",
                    call_id,
                    company_id,
                    ts,
                    {
                        "source": citation.get("source") or "",
                        "excerpt": citation.get("excerpt") or "",
                    },
                )
            )
    return events


def _from_bookings(
    record: dict[str, Any],
    new_image: dict[str, Any],
    old_image: dict[str, Any],
    image: dict[str, Any],
) -> list[dict[str, Any]]:
    booking_id = image.get("bookingId", "")
    call_id = image.get("callId", "") or booking_id
    company_id = image.get("companyId", "")
    ts = image.get("updatedAt") or _now_iso()

    old_slots = (old_image or {}).get("slots") or {}
    new_slots = (new_image or {}).get("slots") or {}
    old_brain = (old_image or {}).get("brain") or {}
    new_brain = (new_image or {}).get("brain") or {}

    events: list[dict[str, Any]] = []

    for slot, value in new_slots.items():
        if old_slots.get(slot) != value and value not in (None, ""):
            events.append(
                _wall_event(
                    "SlotSaved",
                    call_id,
                    company_id,
                    ts,
                    {"slot": slot, "value": str(value), "bookingId": booking_id},
                )
            )

    if new_brain and new_brain != old_brain and "price" in new_brain:
        events.append(
            _wall_event(
                "BrainEstimate",
                call_id,
                company_id,
                ts,
                {
                    "serviceType": new_brain.get("serviceType", ""),
                    "price": _as_float(new_brain.get("price")),
                    "currency": new_brain.get("currency", ""),
                    "needsPhotos": bool(new_brain.get("needsPhotos", False)),
                },
            )
        )
    return events


def _parse_seq(sk: str) -> int:
    try:
        return int(sk.split("#", 1)[1])
    except (IndexError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _wall_event(
    type_name: str,
    call_id: str,
    company_id: str,
    timestamp: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "callId": call_id or "unknown",
        "companyId": company_id or "unknown",
        "timestamp": timestamp,
        "type": type_name,
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# AppSync IAM-signed mutation
# ---------------------------------------------------------------------------


_session: boto3.session.Session | None = None


def _signed_request(payload: dict[str, Any]) -> AWSRequest:
    global _session
    if _session is None:
        _session = boto3.session.Session()
    credentials = _session.get_credentials().get_frozen_credentials()
    request = AWSRequest(
        method="POST",
        url=APPSYNC_URL,
        data=json.dumps(payload),
        headers={"content-type": "application/json"},
    )
    SigV4Auth(credentials, "appsync", AWS_REGION).add_auth(request)
    return request


def _publish(event: dict[str, Any]) -> None:
    if not APPSYNC_URL:
        logger.info("APPSYNC_GRAPHQL_URL not set; skipping publish: %s", event["type"])
        return
    payload = {
        "query": PUBLISH_MUTATION,
        "variables": {
            "event": {
                "callId": event["callId"],
                "companyId": event["companyId"],
                "timestamp": event["timestamp"],
                "type": event["type"],
                "payload": json.dumps(event.get("payload") or {}),
            }
        },
    }
    signed = _signed_request(payload)
    try:
        import urllib.request

        req = urllib.request.Request(
            signed.url,
            data=signed.body if isinstance(signed.body, (bytes, bytearray)) else signed.body.encode("utf-8"),
            headers=dict(signed.headers),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
        logger.info("APPSYNC_PUBLISH %s -> %s", event["type"], body[:200])
    except Exception as exc:  # pragma: no cover - depends on AWS env
        logger.warning("AppSync publish failed for %s: %s", event["type"], exc)


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def handler(event, context):  # noqa: ANN001
    records: Iterable[dict[str, Any]] = event.get("Records", []) or []
    published: list[dict[str, Any]] = []
    for record in records:
        for wall in normalize_record(record):
            _publish(wall)
            published.append(wall)
    return {"published": len(published), "events": published}
