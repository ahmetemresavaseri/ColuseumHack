"""Atrium Input Agent Lambda — Stage 2 skeleton (WebRTC architecture).

Invoked by the **audio API Gateway WebSocket** ($connect / $message / $disconnect
routes). At Stage 2 this is just an echo skeleton:
  - $connect:    log the new connection, return a 200 OK greeting payload
  - $message:    log the message size, send back a placeholder ack
  - $disconnect: log the close, no-op

Stage 3 wires the real audio loop:
  - $message PCM frames → Amazon Transcribe Streaming
  - Final transcripts   → Bedrock Claude Sonnet 4.6 (Converse + tool-use)
  - Claude text reply   → Amazon Polly Neural (synthesize_speech) → MP3 frames
                          posted back via apigatewaymanagementapi.post_to_connection

Allow-list note: this Lambda intentionally does NOT use Amazon Connect or
Bedrock Nova Sonic — neither is on the hackathon allow-list. The voice loop
is Transcribe + Claude Sonnet 4.6 + Polly, all three explicitly allowed.

Event shape (API GW WS): see
https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-websocket-api-mapping-template-reference.html
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

PERSONA_NAME = os.environ.get("PERSONA_NAME", "Sarah")
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Sparkle Cleaning")


def lambda_handler(event, context):
    logger.info("WS_EVENT %s", json.dumps(event, default=str)[:2000])

    request_context = event.get("requestContext", {}) or {}
    route_key = request_context.get("routeKey", "unknown")
    connection_id = request_context.get("connectionId", "unknown")

    if route_key == "$connect":
        logger.info("STAGE2_CONNECT connection_id=%s", connection_id)
        return {"statusCode": 200, "body": "connected"}

    if route_key == "$disconnect":
        logger.info("STAGE2_DISCONNECT connection_id=%s", connection_id)
        return {"statusCode": 200, "body": "disconnected"}

    # $default / $message
    body_raw = event.get("body", "") or ""
    logger.info(
        "STAGE2_MESSAGE connection_id=%s bytes=%d",
        connection_id, len(body_raw),
    )

    placeholder = (
        f"Hello, this is {PERSONA_NAME} from {COMPANY_NAME}. "
        f"This is the Atrium input agent Lambda speaking — stage 2, WebRTC architecture. "
        f"Stage 3 will replace this echo with a live Transcribe + Claude + Polly loop."
    )
    return {
        "statusCode": 200,
        "body": json.dumps({"ack": "stage2", "greeting": placeholder, "stage": "2"}),
    }


# Keep the legacy name `handler` as an alias so existing wiring doesn't break.
handler = lambda_handler
