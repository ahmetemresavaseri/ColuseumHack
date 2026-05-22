"""Live Call Wall REST endpoint.

Phase 1 surface: simple JSON snapshot of the active calls for a tenant. The
frontend prefers the Wall WebSocket; this REST endpoint exists for the
polling fallback (`VITE_WALL_API_URL`) and for the local simulator that
streams the same event contract.

Database constraint: this handler does NOT read DynamoDB. It returns either
an empty active-call list or a small canned demo payload based on query
parameters. Teammates own the durable read path; when they wire it, the
response shape below should be preserved.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _demo_active_call(company_id: str) -> dict[str, Any]:
    """Single canned active-call payload used for the demo poll path."""
    return {
        "callId": "demo-call-001",
        "companyId": company_id,
        "companyName": os.environ.get("COMPANY_NAME", "Glanz AG"),
        "caller": "+41 44 000 00 00",
        "locale": "de-CH",
        "startedAt": _now(),
        "status": "Live",
        "slots": {
            "when": "tomorrow",
            "what": "MOVE_OUT_CLEANING",
            "area": "85 m2",
            "rooms": "4",
            "urgency": "urgent",
            "location": "Zurich Altstadt",
        },
        "transcript": [
            {"seq": 1, "speaker": "Agent", "text": "Hello, Glanz AG. How can I help?"},
            {"seq": 2, "speaker": "Caller", "text": "I need move-out cleaning tomorrow."},
        ],
        "citations": [
            {"source": "Pricelist.pdf p.3", "excerpt": "Move-out cleaning starts with a base fee."},
        ],
        "brain": {
            "serviceType": "MOVE_OUT_CLEANING",
            "price": 703.13,
            "currency": "CHF",
            "needsPhotos": False,
        },
    }


def _response(payload: dict[str, Any], status: int = 200) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            # CORS for local Vite dev server; tighten in production.
            "access-control-allow-origin": "*",
        },
        "body": json.dumps(payload),
    }


def handler(event, context):  # noqa: ANN001 - Lambda signature
    qs = event.get("queryStringParameters") or {}
    company_id = qs.get("company") or qs.get("companyId") or "demo-tenant"
    mode = (qs.get("mode") or "active").lower()

    if mode == "demo":
        # Used by the polling fallback before any real call is wired.
        return _response({"calls": [_demo_active_call(company_id)], "events": []})

    # TODO(teammates): when the durable read path lands, this is the spot to
    # query the `Calls` table by `companyId`. Until then we return an empty
    # active-call list; the frontend will continue to show its empty state.
    return _response({"calls": [], "events": []})
