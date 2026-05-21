"""Tool dispatch for Nova Sonic function calls."""
from __future__ import annotations

import json
import os
from typing import Any, Callable

import boto3

from ddb import save_slot
from kb import kb_lookup


Tool = Callable[[dict[str, Any]], dict[str, Any]]


def _compute_price(args: dict[str, Any]) -> dict[str, Any]:
    function_name = os.environ.get("BRAIN_FUNCTION_NAME", "atrium-brain")
    response = boto3.client("lambda").invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(args).encode("utf-8"),
    )
    payload = response["Payload"].read().decode("utf-8")
    return json.loads(payload or "{}")


def _save_slot(args: dict[str, Any]) -> dict[str, Any]:
    return save_slot(
        call_id=args["callId"],
        booking_id=args["bookingId"],
        slot=args["slot"],
        value=args["value"],
    )


def _kb_lookup(args: dict[str, Any]) -> dict[str, Any]:
    return kb_lookup(
        question=args["question"],
        company_id=args["companyId"],
        top_k=int(args.get("topK", 4)),
    )


def _end_call(args: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ended", "reason": args.get("reason", "completed")}


TOOLS: dict[str, Tool] = {
    "kb_lookup": _kb_lookup,
    "save_slot": _save_slot,
    "compute_price": _compute_price,
    "end_call": _end_call,
}


def dispatch_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOLS:
        return {"error": f"unknown_tool:{name}"}
    return TOOLS[name](args)
