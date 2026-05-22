"""Atrium Input Agent Lambda — Phase 1 live call spine.

Three entry shapes, dispatched by `lambda_handler`:

1. **Connect contact-flow event** (`Details.ContactData`) — initial Lambda
   invocation from `InvokeLambdaFunction` at the top of the contact flow.
   Initializes Calls/Bookings rows and returns
   `{ greeting, callId, bookingId, companyId, locale, ... }` as flat string
   contact attributes. The contact flow then sets those as Lex session
   attributes on the `GetCustomerInput` block.

2. **Lex V2 fulfillment event** (`sessionState` + `inputTranscript`) — fires
   on every caller turn (DialogCodeHook) and at fulfillment. We extract
   slots from the transcript, persist them, and tell Lex which slot to
   elicit next.

3. **WebSocket fallback** (`requestContext.routeKey`) — original browser-mic
   path kept as a manual-test fallback. Default deployment routes phone
   calls via Connect+Lex; this path is gated by env (`ENABLE_WS_FALLBACK`)
   so it can be wired only when explicitly needed.

Persistence: every state change goes through `ddb.py` (which is what
`slot_adapter.py` calls when `DDB_BACKEND=real`). DynamoDB Streams on
`Calls` and `Bookings` are consumed by `lambdas/stream_to_appsync/handler.py`
which publishes Wall events to AppSync.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from typing import Any, Callable

import connect_event
import ddb
import events as wall_events
import lex_v2
from bedrock_client import BedrockClaudeClient
from polly_client import PollyClient
from session import CallSession, drop_session, get_session, new_session
from tool_dispatcher import dispatch_tool
from slot_adapter import save_slot as save_slot_adapter
from slot_extraction import apply_extractions, extract_slots_deterministic
from slot_state import REQUIRED_SLOTS, SlotState
from transcribe_client import TranscribeClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

EmitFn = Callable[[dict[str, Any]], None]

PERSONA_NAME = os.environ.get("PERSONA_NAME", "Sarah")
DEFAULT_COMPANY_NAME = os.environ.get("COMPANY_NAME", "Sparkle Cleaning")
DEFAULT_COMPANY_ID = os.environ.get("COMPANY_ID", "demo-tenant")
USE_REAL_DDB = os.environ.get("DDB_BACKEND", "mock").lower() == "real"


# ---------------------------------------------------------------------------
# Lex V2 path — multi-turn slot collection over the phone via Connect+Lex
# ---------------------------------------------------------------------------


def handle_lex_event(event: dict[str, Any], context: Any | None = None) -> dict[str, Any]:
    attrs = lex_v2.get_session_attributes(event)
    first_turn = not attrs.get("callId")

    # When Lex is invoked through Connect, its sessionId IS the Connect
    # ContactId — which is also what the Connect bootstrap Lambda wrote as
    # `callId` on Calls#meta. Re-using it lets the Lex code-hook pick up the
    # tenant context (companyId, caller, locale) that Connect already
    # resolved, without needing LexSessionAttributes plumbing through the
    # contact flow (which Connect rejects with InvalidContactFlowException
    # for `$.External.*` references).
    lex_session_id = event.get("sessionId") or ""
    call_id = attrs.get("callId") or lex_session_id or f"call-{uuid.uuid4().hex[:12]}"
    booking_id = attrs.get("bookingId") or ""
    company_id = attrs.get("companyId") or DEFAULT_COMPANY_ID
    caller = attrs.get("caller", "")
    locale = ""
    company_name = ""

    if USE_REAL_DDB and first_turn:
        try:
            meta = ddb.get_call_meta(call_id)
        except Exception as exc:  # pragma: no cover
            logger.warning("get_call_meta failed: %s", exc)
            meta = {}
        if meta:
            company_id = meta.get("companyId") or company_id
            booking_id = booking_id or meta.get("bookingId") or f"booking-{uuid.uuid4().hex[:12]}"
            caller = meta.get("caller", caller)
            locale = meta.get("locale", "")
            company_name = meta.get("companyName", "")
        else:
            booking_id = booking_id or f"booking-{uuid.uuid4().hex[:12]}"
            try:
                company = ddb.get_company(company_id) or {}
                if company:
                    company_id = company.get("companyId", company_id)
                    locale = company.get("locale", "")
                    company_name = company.get("name", "")
            except Exception as exc:  # pragma: no cover
                logger.warning("get_company lookup failed: %s", exc)

        try:
            ddb.start_call(
                call_id,
                booking_id,
                company_id,
                caller=caller,
                locale=locale,
                company_name=company_name,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Lex first-turn start_call failed: %s", exc)
    elif not booking_id:
        booking_id = f"booking-{uuid.uuid4().hex[:12]}"

    # Source of truth for slot values across turns is Lex's `intent.slots`
    # map (Lambda is stateless; DynamoDB is for the Wall fan-out, not for
    # rehydrating between Lex hops). We mirror the slots into our SlotState
    # for the extractor, then write back the updated map.
    #
    # If Lex matched FallbackIntent (caller's first utterance didn't match
    # any CollectBooking sample), promote to CollectBooking using the
    # secondary interpretation Lex provided. CollectBooking owns the 6
    # slots we elicit.
    intent = event.get("sessionState", {}).get("intent", {}) or {}
    if intent.get("name") == "FallbackIntent":
        intent = _promote_to_collect_booking(event) or intent
    lex_slots: dict[str, Any] = dict(intent.get("slots") or {})

    # Lex's built-in slot elicitation stores the *whole caller utterance* as
    # the slot value (because we use AMAZON.FreeFormInput). Run each captured
    # value back through our deterministic extractor so e.g.
    #   when="Tomorrow for 85 square meters" → when="tomorrow"
    # Persist each cleaned value through the adapter so the Wall sees it —
    # otherwise the cleaned values only live in Lex's session and never reach
    # the SlotSaved stream.
    lex_slots, cleaned_pairs = _clean_lex_slots(lex_slots)
    for slot, value in cleaned_pairs:
        save_slot_adapter(
            call_id=call_id, booking_id=booking_id, slot=slot, value=value,
        )
    state = _state_from_lex_slots(lex_slots)

    transcript = lex_v2.get_input_transcript(event)
    if transcript:
        pairs = extract_slots_deterministic(transcript, state)
        accepted = apply_extractions(state, pairs)
        for slot, value in accepted:
            save_slot_adapter(
                call_id=call_id,
                booking_id=booking_id,
                slot=slot,
                value=value,
            )
            lex_slots[slot] = _to_lex_slot_value(value)

        if USE_REAL_DDB:
            try:
                meta = ddb.get_call_meta(call_id)
                seq = int(meta.get("turnSeq") or 0) + 1
                ddb.append_turn(
                    call_id,
                    seq,
                    "Caller",
                    transcript,
                    company_id=meta.get("companyId") or company_id,
                )
            except Exception as exc:  # pragma: no cover
                logger.warning("append_turn failed: %s", exc)

    attrs["callId"] = call_id
    attrs["bookingId"] = booking_id
    attrs["companyId"] = company_id

    # Brain mid-call: once `what` + `area` are known, invoke the Brain Lambda
    # for a live price estimate. The Wall picks the result up via the
    # `Bookings#current.brain` → DDB Streams → `BrainEstimate` event chain.
    # Skip if we already computed it this session (sessionAttribute pin).
    if (
        USE_REAL_DDB
        and state.what
        and state.area is not None
        and attrs.get("brainComputed") != "1"
    ):
        try:
            _maybe_compute_brain(
                call_id=call_id,
                booking_id=booking_id,
                company_id=company_id,
                state=state,
            )
            attrs["brainComputed"] = "1"
        except Exception as exc:  # pragma: no cover - depends on AWS env
            logger.warning("compute_price failed: %s", exc)

    next_slot = next(
        (s for s in lex_v2.REQUIRED_SLOT_ORDER if not _slot_filled(state, s)),
        None,
    )

    if next_slot is None or lex_v2.get_invocation_source(event) == "FulfillmentCodeHook":
        # Read back the brain estimate (computed mid-call) so we can speak
        # the price + feasibility before hanging up.
        close_message = _compose_close_message(booking_id, state)
        if USE_REAL_DDB:
            try:
                ddb.end_call(call_id, booking_id, reason="completed")
            except Exception as exc:  # pragma: no cover
                logger.warning("end_call failed: %s", exc)
        return lex_v2.close_session(
            close_message,
            session_attributes=attrs,
            intent_name=intent.get("name", lex_v2.INTENT_NAME),
        )

    return lex_v2.elicit_slot(
        next_slot,
        lex_v2.PROMPTS[next_slot],
        session_attributes=attrs,
        intent_name=intent.get("name", lex_v2.INTENT_NAME),
        intent_slots=lex_slots,
    )


def _to_lex_slot_value(value: Any) -> dict[str, Any]:
    """Wrap a raw value in the Lex V2 slot shape."""
    return {
        "value": {
            "originalValue": str(value),
            "interpretedValue": str(value),
            "resolvedValues": [str(value)],
        }
    }


def _promote_to_collect_booking(event: dict[str, Any]) -> dict[str, Any] | None:
    """Pick the CollectBooking interpretation out of Lex's alternatives.

    Lex sends a list of `interpretations`. When confidence on CollectBooking
    is below Lex's match threshold (often the case in voice), Lex picks
    FallbackIntent as the matched intent but still surfaces CollectBooking
    as a candidate. We promote it so the rest of the handler can elicit
    the right slots.
    """
    for interp in event.get("interpretations") or []:
        ent = interp.get("intent") or {}
        if ent.get("name") == "CollectBooking":
            return ent
    return {"name": "CollectBooking", "slots": {}, "state": "InProgress"}


def _clean_lex_slots(lex_slots: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    """Run each Lex auto-captured slot value through the deterministic extractor.

    Lex stores `originalValue=<full caller utterance>` when using
    AMAZON.FreeFormInput. If the extractor finds a clean canonical value for
    the *same* slot inside that text, replace the Lex value with ours.

    Returns the cleaned dict AND a list of `(slot, canonical_value)` pairs
    that actually got cleaned, so the handler can persist them.
    """
    cleaned: dict[str, Any] = {}
    changed: list[tuple[str, Any]] = []
    for slot, payload in lex_slots.items():
        if not isinstance(payload, dict):
            cleaned[slot] = payload
            continue
        raw = ((payload.get("value") or {}).get("originalValue") or "").strip()
        if not raw:
            cleaned[slot] = payload
            continue
        pairs = extract_slots_deterministic(raw, SlotState())
        canonical = next((v for s, v in pairs if s == slot), None)
        if canonical is not None and str(canonical) != raw:
            cleaned[slot] = _to_lex_slot_value(canonical)
            changed.append((slot, canonical))
        else:
            cleaned[slot] = payload
    return cleaned, changed


def _state_from_lex_slots(lex_slots: dict[str, Any]) -> SlotState:
    """Convert Lex's per-turn slot dict back into our SlotState."""
    flat: dict[str, Any] = {}
    for slot, payload in lex_slots.items():
        if isinstance(payload, dict):
            value = (payload.get("value") or {}).get("interpretedValue")
        else:
            value = payload
        if value is not None and value != "":
            flat[slot] = value
    # `area` / `rooms` come back as strings from Lex; SlotState expects raw.
    return SlotState.from_dict(flat)


def _slot_filled(state: SlotState, slot: str) -> bool:
    value = getattr(state, slot, None)
    return value not in (None, "")


_SERVICE_SPOKEN = {
    "MOVE_OUT_CLEANING": "move-out cleaning",
    "OFFICE_CLEANING": "office cleaning",
    "CONSTRUCTION_CLEANING": "construction cleaning",
    "WINDOW_CLEANING": "window cleaning",
    "FACILITY_MAINTENANCE": "facility maintenance",
}

_CURRENCY_SPOKEN = {
    "CHF": "Swiss francs",
    "EUR": "euros",
    "USD": "US dollars",
    "GBP": "pounds",
}


def _compose_close_message(booking_id: str, state: SlotState) -> str:
    """Speak the price + feasibility back to the caller before hanging up."""
    fallback = "All set — I have everything I need. Thanks for calling. Goodbye!"
    if not USE_REAL_DDB:
        return fallback
    try:
        booking = ddb.get_booking(booking_id) or {}
    except Exception as exc:  # pragma: no cover
        logger.warning("close: get_booking failed: %s", exc)
        return fallback

    brain = booking.get("brain") or {}
    price = brain.get("price")
    if price is None:
        return fallback

    service = _SERVICE_SPOKEN.get(brain.get("serviceType", ""), "your cleaning")
    currency = _CURRENCY_SPOKEN.get(str(brain.get("currency", "")), str(brain.get("currency", "")))
    try:
        price_str = f"{float(price):.0f}"
    except (TypeError, ValueError):
        price_str = str(price)

    parts = [f"For the {service}, the estimate is {price_str} {currency}."]
    feasibility = brain.get("feasibility") or {}
    status = feasibility.get("status")
    reasons = feasibility.get("reasons") or []
    if status == "needs_review":
        if "photos_required" in reasons:
            parts.append("We'll send a short email asking for a couple of photos before we confirm.")
        elif "no_crew_assigned" in reasons:
            parts.append("Someone from our team will reach out shortly to confirm a crew.")
        elif "large_area" in reasons or "large_rooms" in reasons or "over_capacity" in reasons:
            parts.append("Because of the size, our team will follow up to confirm the final quote.")
        else:
            parts.append("Our team will follow up to confirm the booking shortly.")
    elif status == "unsupported":
        parts = ["I'm sorry, that service isn't one we offer right now. Our team will be in touch."]
    else:
        parts.append("We'll send a confirmation to your email. Thanks for calling Glanz AG. Goodbye!")
        return " ".join(parts)
    parts.append("Goodbye!")
    return " ".join(parts)


def _maybe_compute_brain(
    *,
    call_id: str,
    booking_id: str,
    company_id: str,
    state: SlotState,
) -> None:
    """Invoke the Brain Lambda once `what` + `area` are filled.

    Writes the estimate into `Bookings#current.brain`; the DDB stream picks
    it up and the wall fan-out publishes a `BrainEstimate` event.
    """
    # Brain wants raw numbers; our extractor stores formatted strings like
    # "85 m2". Strip non-numeric chars before forwarding.
    import re as _re
    def _num(value):
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            return value
        m = _re.search(r"-?\d+(?:\.\d+)?", str(value))
        return float(m.group(0)) if m else None

    slots = {
        "what": state.what,
        "area": _num(state.area),
        "rooms": _num(state.rooms),
        "urgency": state.urgency,
        "when": state.when,
        "email": state.email,
    }
    result = dispatch_tool(
        "compute_price",
        {"companyId": company_id, "callId": call_id, "bookingId": booking_id, "slots": slots},
    )
    if result.get("status") in ("brain_not_configured", "brain_error"):
        logger.info("compute_price skipped: %s", result.get("status"))
        return
    if not isinstance(result, dict) or "price" not in result:
        logger.info("compute_price returned no price: %s", str(result)[:200])
        return
    brain_payload = {
        "serviceType": result.get("serviceType", ""),
        "price": result.get("price"),
        "currency": result.get("currency", ""),
        "needsPhotos": bool(result.get("needsPhotos", False)),
    }
    if result.get("crew"):
        brain_payload["crew"] = result["crew"]
    if result.get("feasibility"):
        brain_payload["feasibility"] = result["feasibility"]
    ddb.save_brain(booking_id, brain_payload)
    logger.info("BRAIN saved: %s", brain_payload)


# ---------------------------------------------------------------------------
# Connect contact-flow path — bootstrap call+booking, return contact attributes
# ---------------------------------------------------------------------------


def handle_connect_event(event: dict[str, Any], context: Any | None = None) -> dict[str, str]:
    contact_id = connect_event.get_contact_id(event)
    caller = connect_event.get_caller_phone(event)
    dialed = connect_event.get_dialed_phone(event)

    company_id = connect_event.get_attribute(event, "companyId") or DEFAULT_COMPANY_ID
    company: dict[str, Any] = {}
    if USE_REAL_DDB:
        try:
            if dialed:
                company = ddb.get_company_by_phone_number(dialed) or {}
            if not company:
                company = ddb.get_company(company_id) or {}
            company_id = company.get("companyId", company_id)
        except Exception as exc:  # pragma: no cover
            logger.warning("company lookup failed: %s", exc)

    company_name = company.get("name") or DEFAULT_COMPANY_NAME
    locale = company.get("locale") or os.environ.get("DEFAULT_LOCALE", "en-US")
    persona = company.get("voicePersonaPrompt") or f"You are {PERSONA_NAME}."

    call_id = contact_id or f"call-{uuid.uuid4().hex[:12]}"
    booking_id = f"booking-{uuid.uuid4().hex[:12]}"

    if USE_REAL_DDB:
        try:
            ddb.start_call(
                call_id,
                booking_id,
                company_id,
                contact_id=contact_id,
                caller=caller,
                locale=locale,
                company_name=company_name,
            )
            ddb.append_turn(
                call_id,
                1,
                "Agent",
                f"Hello, {company_name}. How can I help you today?",
                company_id=company_id,
            )
        except Exception as exc:  # pragma: no cover - DDB transient
            logger.warning("start_call failed: %s", exc)

    return connect_event.respond(
        {
            "callId": call_id,
            "bookingId": booking_id,
            "companyId": company_id,
            "companyName": company_name,
            "locale": locale,
            "persona": persona[:512],
            "greeting": f"Hello, {company_name}. How can I help you today?",
        }
    )


# ---------------------------------------------------------------------------
# WebSocket fallback (manual testing only) — gated by ENABLE_WS_FALLBACK
# ---------------------------------------------------------------------------


def handle_websocket_event(event: dict[str, Any], context: Any | None = None) -> dict[str, Any]:
    if os.environ.get("ENABLE_WS_FALLBACK", "0") != "1":
        # Default deployment doesn't expose this. Keep the code path for
        # local browser-mic testing.
        return {"statusCode": 403, "body": "ws_fallback_disabled"}

    rc = event.get("requestContext", {}) or {}
    route_key = rc.get("routeKey", "$default")
    connection_id = rc.get("connectionId", "")
    query = event.get("queryStringParameters") or {}
    company_id = query.get("company") or query.get("companyId") or DEFAULT_COMPANY_ID

    endpoint = _ws_endpoint(event)
    emit = _post_emit(connection_id, endpoint)

    if route_key == "$connect":
        new_session(
            company_id=company_id,
            connection_id=connection_id,
            company_name=query.get("companyName") or DEFAULT_COMPANY_NAME,
        )
        return {"statusCode": 200, "body": "connected"}

    session = get_session(connection_id=connection_id)

    if route_key == "$disconnect":
        if session is not None:
            handle_end_call(session, emit, reason="disconnect")
            drop_session(session)
        return {"statusCode": 200, "body": "disconnected"}

    if session is None:
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
        handle_audio_frame(session, raw, TranscribeClient())
        return {"statusCode": 200, "body": "frame_ack"}

    return {"statusCode": 200, "body": "ignored"}


# ---------------------------------------------------------------------------
# Local/manual test helpers used by the WS fallback and the simulate script
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
    if not session.started:
        return
    transcribe.push_audio(frame)


def handle_end_call(session: CallSession, emit: EmitFn, reason: str = "completed") -> None:
    if session.ended:
        return
    session.ended = True
    emit(wall_events.call_ended(session.call_id, session.company_id, reason=reason))


# ---------------------------------------------------------------------------
# Lambda entry point — pick the right path by event shape
# ---------------------------------------------------------------------------


def _post_emit(connection_id: str, endpoint: str | None) -> EmitFn:
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
    body = event.get("body")
    if body is None:
        return None, None, None
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(body)
        except Exception:
            return None, None, None
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


def lambda_handler(event, context):  # noqa: ANN001
    logger.info("ATRIUM_EVENT %s", json.dumps(event, default=str)[:1500])

    if lex_v2.is_lex_event(event):
        return handle_lex_event(event, context)

    if connect_event.is_connect_event(event):
        return handle_connect_event(event, context)

    if isinstance(event, dict) and "requestContext" in event and "routeKey" in (event.get("requestContext") or {}):
        return handle_websocket_event(event, context)

    return {"statusCode": 400, "body": "unrecognized_event"}


# Legacy alias
handler = lambda_handler


# ---------------------------------------------------------------------------
# Local-only helper for tests / simulators (no AWS dependency)
# ---------------------------------------------------------------------------


def make_local_session(company_id: str = DEFAULT_COMPANY_ID) -> tuple[CallSession, wall_events.EventSink]:
    _ = BedrockClaudeClient()  # boundary import is intentional
    _ = PollyClient()
    sink = wall_events.EventSink()
    session = new_session(company_id=company_id)
    return session, sink


# Quiet noqa for unused REQUIRED_SLOTS re-export (still useful elsewhere).
_ = REQUIRED_SLOTS
