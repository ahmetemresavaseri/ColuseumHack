"""Atrium Input Agent Lambda — Phase 1 live call spine.

Routes (API Gateway WebSocket):
  $connect      — register a connection, no DB writes
  $disconnect   — close the session, emit CallEnded
  $default      — text/JSON control messages and PCM frames

Client message contract (text/JSON frames):
  { "action": "start_call",  ...metadata }
  { "action": "audio_frame", "frame_b64": "..."  }   # also accepts binary frames
  { "action": "text_turn",   "text": "..."  }        # local/manual testing
  { "action": "end_call",    "reason": "..." }

Server message contract (text/JSON frames):
  { "type": "agent_text", "text": "..."  }
  { "type": "control",    "control": "agent_speaking_start" | ... }
  { "type": "<WallEvent>" }                          # mirror of the wall contract

The handler routes every state change through a small `emit` callable. In
production this is closed over `apigatewaymanagementapi.post_to_connection`;
in tests/local sims it is `events.EventSink`. Either way the JSON shape is
identical to `web/src/lib/types.ts`.

Database constraint: this handler must not call DynamoDB directly. All slot
writes go through `slot_adapter.save_slot`; all KB lookups go through
`tool_dispatcher`. Both have safe no-AWS fallbacks.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Callable

import events as wall_events
from bedrock_client import BedrockClaudeClient
from polly_client import PollyClient
from session import CallSession, drop_session, get_session, new_session
from slot_adapter import save_slot as save_slot_adapter
from slot_extraction import apply_extractions, extract_slots_deterministic
from transcribe_client import TranscribeClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

EmitFn = Callable[[dict[str, Any]], None]

PERSONA_NAME = os.environ.get("PERSONA_NAME", "Sarah")
DEFAULT_COMPANY_NAME = os.environ.get("COMPANY_NAME", "Sparkle Cleaning")
DEFAULT_COMPANY_ID = os.environ.get("COMPANY_ID", "demo-tenant")


# ---------------------------------------------------------------------------
# Public API used by tests, local sims, and the real Lambda entry point.
# ---------------------------------------------------------------------------


def handle_start_call(
    session: CallSession,
    emit: EmitFn,
    *,
    company_name: str | None = None,
    caller: str | None = None,
    locale: str | None = None,
) -> None:
    if company_name:
        session.company_name = company_name
    if caller:
        session.caller = caller
    if locale:
        session.locale = locale
    session.started = True
    emit(
        wall_events.call_started(
            session.call_id,
            session.company_id,
            company_name=session.company_name,
            caller=session.caller,
            locale=session.locale,
        )
    )


def handle_text_turn(session: CallSession, text: str, emit: EmitFn) -> None:
    """Run a finalized caller utterance through slot extraction.

    Phase 1 path; identical to what the Transcribe-driven path calls into.
    """
    text = (text or "").strip()
    if not text:
        return
    seq = session.next_seq()
    emit(
        wall_events.transcript_turn(
            session.call_id,
            session.company_id,
            seq=seq,
            speaker="Caller",
            text=text,
        )
    )
    pairs = extract_slots_deterministic(text, session.slots)
    accepted = apply_extractions(session.slots, pairs)
    for slot, value in accepted:
        result = save_slot_adapter(
            call_id=session.call_id,
            booking_id=session.booking_id,
            slot=slot,
            value=value,
        )
        emit(
            wall_events.slot_saved(
                session.call_id,
                session.company_id,
                slot=slot,
                value=result.get("value", value),
                booking_id=result.get("bookingId", session.booking_id),
            )
        )


def handle_agent_say(session: CallSession, text: str, emit: EmitFn) -> None:
    """Emit transcript + agent-speaking control for an agent utterance."""
    if not text:
        return
    seq = session.next_seq()
    emit(wall_events.agent_speaking_start(session.call_id, session.company_id))
    emit(
        wall_events.transcript_turn(
            session.call_id,
            session.company_id,
            seq=seq,
            speaker="Agent",
            text=text,
        )
    )
    emit(wall_events.agent_speaking_end(session.call_id, session.company_id))


def handle_audio_frame(session: CallSession, frame: bytes, transcribe: TranscribeClient) -> None:
    """Forward a PCM16 frame to Transcribe Streaming.

    Phase 1 leaves the bidirectional stream as a stub; the handler still
    accepts frames so the wire protocol is exercised end-to-end. The text
    side of the pipeline (slot extraction, wall events) is reachable via
    `handle_text_turn` for local testing.
    """
    if not session.started:
        return
    transcribe.push_audio(frame)


def handle_end_call(session: CallSession, emit: EmitFn, reason: str = "completed") -> None:
    if session.ended:
        return
    session.ended = True
    emit(
        wall_events.call_ended(
            session.call_id,
            session.company_id,
            reason=reason,
        )
    )


# ---------------------------------------------------------------------------
# Lambda entry point (API Gateway WebSocket)
# ---------------------------------------------------------------------------


def _post_emit(connection_id: str, endpoint: str | None) -> EmitFn:
    """Return an `emit(event)` that posts JSON back to the WS connection."""
    if not endpoint:
        return lambda _evt: None
    try:
        import boto3  # type: ignore
    except ImportError:  # pragma: no cover
        return lambda _evt: None
    client = boto3.client("apigatewaymanagementapi", endpoint_url=endpoint)

    def _emit(event: dict[str, Any]) -> None:
        try:
            client.post_to_connection(
                ConnectionId=connection_id,
                Data=json.dumps(event).encode("utf-8"),
            )
        except Exception as exc:  # pragma: no cover - depends on AWS env
            logger.warning("post_to_connection failed: %s", exc)

    return _emit


def _ws_endpoint(event: dict[str, Any]) -> str | None:
    rc = event.get("requestContext", {}) or {}
    domain = rc.get("domainName")
    stage = rc.get("stage")
    if domain and stage:
        return f"https://{domain}/{stage}"
    return None


def _decode_body(event: dict[str, Any]) -> tuple[str | None, bytes | None, dict[str, Any] | None]:
    """Return (text, bytes, parsed_json) for the incoming WS message."""
    body = event.get("body")
    if body is None:
        return None, None, None
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(body)
        except Exception:
            return None, None, None
        # Heuristic: if it parses as JSON treat as control message, else PCM.
        try:
            return raw.decode("utf-8"), None, json.loads(raw.decode("utf-8"))
        except Exception:
            return None, raw, None
    if isinstance(body, str):
        try:
            return body, None, json.loads(body)
        except json.JSONDecodeError:
            return body, None, None
    return None, None, None


def lambda_handler(event, context):  # noqa: ANN001 - Lambda signature
    logger.info("WS_EVENT %s", json.dumps(event, default=str)[:1500])

    rc = event.get("requestContext", {}) or {}
    route_key = rc.get("routeKey", "$default")
    connection_id = rc.get("connectionId", "")
    query = event.get("queryStringParameters") or {}
    company_id = query.get("company") or query.get("companyId") or DEFAULT_COMPANY_ID

    endpoint = _ws_endpoint(event)
    emit = _post_emit(connection_id, endpoint)

    if route_key == "$connect":
        session = new_session(
            company_id=company_id,
            connection_id=connection_id,
            company_name=query.get("companyName") or DEFAULT_COMPANY_NAME,
        )
        logger.info(
            "CONNECT call_id=%s company_id=%s connection_id=%s",
            session.call_id,
            company_id,
            connection_id,
        )
        return {"statusCode": 200, "body": "connected"}

    session = get_session(connection_id=connection_id)

    if route_key == "$disconnect":
        if session is not None:
            handle_end_call(session, emit, reason="disconnect")
            drop_session(session)
        return {"statusCode": 200, "body": "disconnected"}

    # $default / message
    if session is None:
        # API Gateway routed a message without a prior $connect; create one.
        session = new_session(company_id=company_id, connection_id=connection_id)

    _text, raw, parsed = _decode_body(event)

    if parsed is not None and isinstance(parsed, dict):
        action = parsed.get("action")
        if action == "start_call":
            handle_start_call(
                session,
                emit,
                company_name=parsed.get("companyName"),
                caller=parsed.get("caller"),
                locale=parsed.get("locale"),
            )
            return {"statusCode": 200, "body": "started"}
        if action == "text_turn":
            handle_text_turn(session, parsed.get("text", ""), emit)
            return {"statusCode": 200, "body": "ok"}
        if action == "end_call":
            handle_end_call(session, emit, reason=parsed.get("reason", "user_hangup"))
            drop_session(session)
            return {"statusCode": 200, "body": "ended"}
        if action == "audio_frame":
            frame_b64 = parsed.get("frame_b64") or ""
            try:
                raw_frame = base64.b64decode(frame_b64)
            except Exception:
                raw_frame = b""
            handle_audio_frame(session, raw_frame, TranscribeClient())
            return {"statusCode": 200, "body": "frame_ack"}

    if raw is not None:
        # Binary PCM frame.
        handle_audio_frame(session, raw, TranscribeClient())
        return {"statusCode": 200, "body": "frame_ack"}

    return {"statusCode": 200, "body": "ignored"}


# Keep the legacy alias for existing wiring.
handler = lambda_handler


# ---------------------------------------------------------------------------
# Convenience for local simulators and tests
# ---------------------------------------------------------------------------


def make_local_session(company_id: str = DEFAULT_COMPANY_ID) -> tuple[CallSession, wall_events.EventSink]:
    """Create a session + in-memory event sink for local testing.

    Phase 1 voice work still in progress: Bedrock Converse + Polly + the
    Transcribe bidi stream are wired through their respective `*_client.py`
    boundaries but the live audio path is not exercised from here. Use this
    helper to drive the text-turn path deterministically.
    """
    _ = BedrockClaudeClient()  # surfaces the import so it can be wired later
    _ = PollyClient()
    sink = wall_events.EventSink()
    session = new_session(company_id=company_id)
    return session, sink
