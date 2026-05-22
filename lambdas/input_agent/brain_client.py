"""Bedrock Claude Sonnet 4.6 turn loop with tool-use.

Each call to `take_turn` advances the conversation by one user turn:
  - appends the user transcript to `messages`
  - loops Converse / tool-use until the model returns a terminal `text` block
  - returns (assistant_text, updated_messages, tool_trace)

`messages` is the Bedrock Converse messages list and is the entire conversation
state — Lex's sessionAttributes is the persistence layer (see lex_handler.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import boto3

from prompts import TOOL_CONFIG
from tool_dispatcher import dispatch_tool

logger = logging.getLogger(__name__)

# The workshop AWS role has explicit-deny on all Anthropic/Meta/Cohere/Mistral
# Bedrock models — only Amazon Nova invocations are permitted. Nova Lite is the
# best fit for live voice tool-use: fast (~0.5-1.5s) and reliable tool-call
# schemas. Override via INPUT_AGENT_MODEL_ID env var when policy allows it.
MODEL_ID = os.environ.get(
    "INPUT_AGENT_MODEL_ID",
    "us.amazon.nova-lite-v1:0",
)

# Nova models emit <thinking>...</thinking> blocks in their text output. The
# caller must never hear those — strip them before returning the spoken reply.
_THINKING_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
MAX_TOOL_ROUNDS = 6  # safety cap to prevent infinite tool-call loops per turn


def _runtime():
    return boto3.client("bedrock-runtime")


def take_turn(
    user_text: str,
    messages: list[dict[str, Any]],
    system_prompt: str,
    call_id: str,
    company_id: str,
    booking_id: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Run one user turn through Claude + tools, return spoken reply."""
    messages = list(messages)
    if user_text:
        messages.append({"role": "user", "content": [{"text": user_text}]})

    tool_trace: list[dict[str, Any]] = []

    for round_idx in range(MAX_TOOL_ROUNDS):
        response = _runtime().converse(
            modelId=MODEL_ID,
            messages=messages,
            system=[{"text": system_prompt}],
            toolConfig=TOOL_CONFIG,
            inferenceConfig={"maxTokens": 512, "temperature": 0.3},
        )
        output = response["output"]["message"]
        messages.append(output)
        stop_reason = response.get("stopReason")
        logger.info("CONVERSE round=%d stop=%s", round_idx, stop_reason)

        if stop_reason != "tool_use":
            return _extract_text(output), messages, tool_trace

        tool_results: list[dict[str, Any]] = []
        for block in output.get("content", []):
            tu = block.get("toolUse")
            if not tu:
                continue
            tool_name = tu["name"]
            tool_args = _inject_context(tu.get("input") or {}, tool_name, call_id, company_id, booking_id)
            try:
                result = dispatch_tool(tool_name, tool_args)
            except Exception as exc:
                logger.exception("TOOL_ERROR %s", tool_name)
                result = {"error": "tool_exception", "message": str(exc)[:300]}
            tool_trace.append({"name": tool_name, "args": tool_args, "result": result})
            tool_results.append(
                {
                    "toolResult": {
                        "toolUseId": tu["toolUseId"],
                        "content": [{"json": result}],
                    }
                }
            )

        messages.append({"role": "user", "content": tool_results})

    logger.warning("MAX_TOOL_ROUNDS reached, returning best-effort text")
    return _extract_text(messages[-1]) or "Let me get back to you on that.", messages, tool_trace


def _extract_text(message: dict[str, Any]) -> str:
    chunks = [b["text"] for b in message.get("content", []) if "text" in b]
    cleaned = []
    for c in chunks:
        c = _THINKING_RE.sub("", c).strip()
        if c:
            cleaned.append(c)
    return " ".join(cleaned)


def _inject_context(
    args: dict[str, Any], tool_name: str, call_id: str, company_id: str, booking_id: str
) -> dict[str, Any]:
    """Fill in identifiers the model often omits, since they're known to us."""
    args = dict(args)
    if tool_name in {"save_slot"}:
        args.setdefault("callId", call_id)
        args.setdefault("bookingId", booking_id)
    if tool_name in {"kb_lookup", "compute_price"}:
        args.setdefault("companyId", company_id)
    if tool_name in {"check_availability", "book_appointment"}:
        cal_id = os.environ.get("GOOGLE_CALENDAR_ID")
        if cal_id:
            args.setdefault("calendarId", cal_id)
    return args


def messages_from_session(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("bad session messages payload, resetting")
        return []


def messages_to_session(messages: list[dict[str, Any]], cap_bytes: int = 6500) -> str:
    """Serialize messages for sessionAttributes; trim oldest pairs if too large.

    Lex V2 caps session attributes ~10KB total; budget 6.5KB for messages.
    """
    payload = json.dumps(messages)
    while len(payload) > cap_bytes and len(messages) > 2:
        # drop the oldest user+assistant pair, keep the system context implicit
        messages = messages[2:]
        payload = json.dumps(messages)
    return payload
