"""Atrium Input Agent Lambda — event-shape dispatcher.

Two callers wire into this Lambda:
  1. **Amazon Lex V2** (CodeHook from a Connect contact flow's Lex GetCustomerInput
     block). This is the live PSTN path. Event has `sessionId` + `inputTranscript`
     and is routed to lex_handler.handle_lex.
  2. **API Gateway WebSocket** ($connect / $message / $disconnect routes) — the
     stage-2 echo skeleton for the future browser-mic Call-now path. Kept so
     existing WS wiring keeps responding while voice work lands.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

PERSONA_NAME = os.environ.get("PERSONA_NAME", "Sarah")
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Sparkle Cleaning")


def _is_lex_event(event: dict) -> bool:
    return (
        isinstance(event, dict)
        and "inputTranscript" in event
        and "sessionState" in event
        and event.get("bot") is not None
    )


def lambda_handler(event, context):
    logger.info("EVENT_TYPE keys=%s", list(event.keys()) if isinstance(event, dict) else type(event).__name__)

    if _is_lex_event(event):
        # Lazy import: only pull boto3 + heavy modules when actually needed
        from lex_handler import handle_lex
        try:
            return handle_lex(event)
        except Exception:
            logger.exception("LEX_HANDLER_ERROR")
            return _lex_error_response(event)

    return _ws_skeleton(event)


def _lex_error_response(event: dict) -> dict:
    session_state = event.get("sessionState") or {}
    attrs = dict(session_state.get("sessionAttributes") or {})
    intent = (session_state.get("intent") or {}).get("name", "FallbackIntent")
    return {
        "sessionState": {
            "sessionAttributes": attrs,
            "dialogAction": {"type": "ElicitIntent"},
            "intent": {"name": intent, "state": "InProgress"},
        },
        "messages": [
            {
                "contentType": "PlainText",
                "content": "Sorry, I am having trouble. Could you say that again?",
            }
        ],
    }


def _ws_skeleton(event: dict) -> dict:
    request_context = event.get("requestContext", {}) or {}
    route_key = request_context.get("routeKey", "unknown")
    connection_id = request_context.get("connectionId", "unknown")

    if route_key == "$connect":
        logger.info("WS_CONNECT connection_id=%s", connection_id)
        return {"statusCode": 200, "body": "connected"}

    if route_key == "$disconnect":
        logger.info("WS_DISCONNECT connection_id=%s", connection_id)
        return {"statusCode": 200, "body": "disconnected"}

    body_raw = event.get("body", "") or ""
    logger.info("WS_MESSAGE connection_id=%s bytes=%d", connection_id, len(body_raw))

    placeholder = (
        f"Hello, this is {PERSONA_NAME} from {COMPANY_NAME}. "
        f"WebSocket browser-mic path is still a stage-2 echo skeleton — the live "
        f"voice path is via Amazon Lex from the Connect contact flow."
    )
    return {
        "statusCode": 200,
        "body": json.dumps({"ack": "stage2", "greeting": placeholder, "stage": "2"}),
    }


# Backwards-compat alias
handler = lambda_handler
