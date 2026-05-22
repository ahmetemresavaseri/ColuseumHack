"""Tool dispatch for the Input Agent.

Used by the Bedrock Claude Converse turn loop: every tool-use block the model
emits is routed through `dispatch_tool(name, args)`. Phase 1 tools are
deliberately thin so the frontend and voice loops can ship before the real
DynamoDB / Brain wiring lands.

Tool surface (Phase 1):
  - save_slot:     adapter-backed (in-memory or DDB), returns canonical shape
  - kb_lookup:     real KB if configured, otherwise safe `kb_not_configured`
  - compute_price: optional Brain Lambda invoke; stub if not configured
  - end_call:      emits a clean end-call payload
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

try:
    import boto3  # type: ignore
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore

from feasibility import assess as assess_feasibility
from kb import kb_lookup
from slot_adapter import save_slot as save_slot_adapter

logger = logging.getLogger(__name__)

Tool = Callable[[dict[str, Any]], dict[str, Any]]


def _compute_price(args: dict[str, Any]) -> dict[str, Any]:
    function_name = os.environ.get("BRAIN_FUNCTION_NAME")
    if not function_name or boto3 is None:
        # Phase 1 does not require the Brain to be online.
        return {"status": "brain_not_configured", "slots": args.get("slots", {})}
    try:
        response = boto3.client("lambda").invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(args).encode("utf-8"),
        )
        payload = response["Payload"].read().decode("utf-8")
        return json.loads(payload or "{}")
    except Exception as exc:  # pragma: no cover - depends on AWS env
        logger.warning("compute_price invoke failed: %s", exc)
        return {"status": "brain_error", "error": str(exc)}


def _save_slot(args: dict[str, Any]) -> dict[str, Any]:
    return save_slot_adapter(
        call_id=args["callId"],
        booking_id=args.get("bookingId", args["callId"]),
        slot=args["slot"],
        value=args["value"],
    )


def _kb_lookup(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return kb_lookup(
            question=args["question"],
            company_id=args.get("companyId", "unknown"),
            top_k=int(args.get("topK", 4)),
        )
    except Exception as exc:  # pragma: no cover - boto3/credentials missing
        logger.warning("kb_lookup failed, returning safe fallback: %s", exc)
        return {
            "answer_context": "",
            "citations": [],
            "status": "kb_not_configured",
        }


def _end_call(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ended",
        "callId": args.get("callId"),
        "reason": args.get("reason", "completed"),
    }


def _feasibility_assessment(args: dict[str, Any]) -> dict[str, Any]:
    """Standalone feasibility check; doesn't need Brain Lambda.

    Brain Lambda already bundles a feasibility result on each compute_price
    response, so most callers get it for free. This dispatcher entry exists
    so an agent (or test harness) can call `feasibility_assessment` without
    invoking the Brain Lambda end-to-end.
    """
    return assess_feasibility(
        slots=args.get("slots", {}) or {},
        brain=args.get("brain", {}) or {},
        crews=args.get("crews") or [],
    )


TOOLS: dict[str, Tool] = {
    "kb_lookup": _kb_lookup,
    "save_slot": _save_slot,
    "compute_price": _compute_price,
    "feasibility_assessment": _feasibility_assessment,
    "end_call": _end_call,
}


def dispatch_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOLS:
        return {"error": f"unknown_tool:{name}"}
    return TOOLS[name](args)
