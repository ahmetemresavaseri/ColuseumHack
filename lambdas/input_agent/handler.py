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
from kb import ANSWER_MIN_SCORE, kb_lookup
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

    transcript = lex_v2.get_input_transcript(event)

    # Conversation phases (sessionAttribute "phase"):
    #   collecting        — eliciting the 5 booking slots
    #   estimate_spoken   — Brain quote spoken; caller can react / ask
    #   asking_location   — collecting the address / area for the booking
    #   any_questions     — final "anything else?" beat before goodbye
    #
    # Each phase has different rules for routing the caller's reply:
    # `estimate_spoken` and `any_questions` intercept the reply BEFORE the
    # slot pipeline runs, so the reply doesn't pollute the location transport
    # slot Lex is technically eliciting.
    phase = attrs.get("phase", "collecting")

    if phase == "estimate_spoken":
        return _react_to_estimate(
            transcript=transcript, event=event, intent=intent,
            lex_slots=lex_slots, attrs=attrs,
            call_id=call_id, booking_id=booking_id, company_id=company_id,
        )

    if phase == "any_questions":
        if transcript and _is_question(transcript):
            # Real question — KB answer, then re-prompt for more questions.
            attrs.pop("awaitingFollowUpQuestion", None)
            return _handle_faq_turn(
                transcript=transcript, event=event, intent=intent,
                lex_slots=lex_slots, attrs=attrs,
                call_id=call_id, booking_id=booking_id, company_id=company_id,
                final=True,
            )
        if (
            transcript
            and _is_affirmative(transcript)
            and attrs.get("awaitingFollowUpQuestion") != "1"
        ):
            # Caller said "yes" — they intend to ask but haven't yet. Invite
            # the actual question; one-shot flag so a second bare "yes" closes.
            attrs["awaitingFollowUpQuestion"] = "1"
            attrs["callId"] = call_id
            attrs["bookingId"] = booking_id
            attrs["companyId"] = company_id
            _log_caller_turn(call_id, company_id, transcript)
            prompt = "Sure, what would you like to know?"
            _log_agent_turn(call_id, company_id, prompt)
            elicited = _just_elicited_slot(lex_slots, transcript)
            if elicited:
                lex_slots.pop(elicited, None)
            return lex_v2.elicit_slot(
                "location", prompt,
                session_attributes=attrs,
                intent_name=intent.get("name", lex_v2.INTENT_NAME),
                intent_slots=lex_slots,
            )
        return _finalize_after_questions(
            transcript=transcript, event=event, intent=intent,
            lex_slots=lex_slots, attrs=attrs,
            call_id=call_id, booking_id=booking_id, company_id=company_id,
        )

    # Phase 3 RAG: if caller asks a question DURING slot collection, answer
    # from KB before the slot pipeline mistakes it for slot input.
    if phase == "collecting" and transcript and _is_question(transcript):
        return _handle_faq_turn(
            transcript=transcript,
            event=event,
            intent=intent,
            lex_slots=lex_slots,
            attrs=attrs,
            call_id=call_id,
            booking_id=booking_id,
            company_id=company_id,
        )

    # Lex's built-in slot elicitation stores the *whole caller utterance* as
    # the slot value (because we use AMAZON.FreeFormInput). Run each captured
    # value back through our deterministic extractor so e.g.
    #   when="Tomorrow for 85 square meters" → when="tomorrow"
    # Persist each cleaned value through the adapter so the Wall sees it —
    # otherwise the cleaned values only live in Lex's session and never reach
    # the SlotSaved stream.
    lex_slots, cleaned_pairs = _clean_lex_slots(lex_slots)
    cleaned_slot_names = {s for s, _ in cleaned_pairs}
    for slot, value in cleaned_pairs:
        save_slot_adapter(
            call_id=call_id, booking_id=booking_id, slot=slot, value=value,
        )
    # If Lex just elicited a slot but the cleaner didn't normalize it (because
    # the raw text is already its own canonical form, e.g. when="in three
    # days"), persist the raw value once so the wall actually shows it.
    elicited_this_turn = _just_elicited_slot(lex_slots, transcript) if transcript else None
    if elicited_this_turn and elicited_this_turn not in cleaned_slot_names:
        payload = lex_slots.get(elicited_this_turn) or {}
        value_dict = (payload.get("value") or {}) if isinstance(payload, dict) else {}
        raw = (value_dict.get("interpretedValue") or value_dict.get("originalValue") or "").strip()
        validated_raw = _validate_slot_value(elicited_this_turn, raw)
        if validated_raw is not None:
            save_slot_adapter(
                call_id=call_id, booking_id=booking_id,
                slot=elicited_this_turn, value=validated_raw,
            )

    state = _state_from_lex_slots(lex_slots)

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

    # Derive urgency from `when` if the caller hasn't said anything urgent-
    # flavored explicitly. Avoids asking "how urgent?" when "in three days"
    # already gave us enough.
    if _slot_filled(state, "when") and not _slot_filled(state, "urgency"):
        derived = _derive_urgency_from_when(str(state.when))
        if derived:
            state.update("urgency", derived)
            lex_slots["urgency"] = _to_lex_slot_value(derived)
            save_slot_adapter(
                call_id=call_id, booking_id=booking_id,
                slot="urgency", value=derived,
            )

    # Phase = "asking_location": validate the address the caller just gave us.
    # If the reply is too short / blank / clearly a question, re-prompt
    # (and answer the question from KB on the way back).
    if phase == "asking_location":
        location_value = str(state.location or "")
        if _slot_filled(state, "location") and _looks_like_location(location_value):
            attrs["phase"] = "any_questions"
            prompt = (
                "Before we wrap up — do you have any questions about our "
                "services, pricing, or scheduling?"
            )
            _log_agent_turn(call_id, company_id, prompt)
            return lex_v2.elicit_slot(
                "location", prompt,
                session_attributes=attrs,
                intent_name=intent.get("name", lex_v2.INTENT_NAME),
                intent_slots=lex_slots,
            )

        # Caller's reply wasn't a usable location — undo the bad save.
        if _slot_filled(state, "location"):
            try:
                save_slot_adapter(
                    call_id=call_id, booking_id=booking_id,
                    slot="location", value="",
                )
            except Exception:  # pragma: no cover
                pass
            lex_slots.pop("location", None)
            state.update("location", None)

        answer_prefix = ""
        citations = []
        if transcript and _is_question(transcript):
            kb_result = kb_lookup(transcript, company_id, top_k=3)
            answer, citations = _compose_faq_answer(kb_result)
            answer_prefix = f"{answer} "

        prompt = (
            f"{answer_prefix}What's the address or area where the cleaning "
            "will take place?"
        )
        _log_agent_turn(
            call_id, company_id, prompt,
            citations=citations or None,
        )
        return lex_v2.elicit_slot(
            "location", prompt,
            session_attributes=attrs,
            intent_name=intent.get("name", lex_v2.INTENT_NAME),
            intent_slots=lex_slots,
        )

    # Phase = "collecting": elicit the next of the 5 booking slots OR, when
    # all 5 are filled, compute the Brain estimate and speak it.
    booking_slot_order = ("when", "what", "area", "rooms", "urgency")
    next_slot = next(
        (s for s in booking_slot_order if not _slot_filled(state, s)),
        None,
    )

    if next_slot is None:
        # All 5 collected. Compute brain (once), speak the estimate, advance.
        if USE_REAL_DDB and attrs.get("brainComputed") != "1":
            try:
                _maybe_compute_brain(
                    call_id=call_id,
                    booking_id=booking_id,
                    company_id=company_id,
                    state=state,
                )
                attrs["brainComputed"] = "1"
            except Exception as exc:  # pragma: no cover
                logger.warning("compute_price failed: %s", exc)
        estimate_text = _compose_estimate_speech(booking_id, state)
        prompt = f"{estimate_text} Does that work for you?"
        attrs["phase"] = "estimate_spoken"
        _log_agent_turn(call_id, company_id, prompt)
        return lex_v2.elicit_slot(
            "location", prompt,
            session_attributes=attrs,
            intent_name=intent.get("name", lex_v2.INTENT_NAME),
            intent_slots=lex_slots,
        )

    prompt = lex_v2.PROMPTS[next_slot]
    _log_agent_turn(call_id, company_id, prompt)
    return lex_v2.elicit_slot(
        next_slot,
        prompt,
        session_attributes=attrs,
        intent_name=intent.get("name", lex_v2.INTENT_NAME),
        intent_slots=lex_slots,
    )


def _log_agent_turn(
    call_id: str,
    company_id: str,
    text: str,
    citations: list[dict[str, Any]] | None = None,
) -> None:
    """Write the Lambda's spoken response to `Calls#turn#NNNNNN` so the wall
    shows both sides of the conversation. When `citations` is supplied, the
    stream fan-out turns each one into a `CitationAdded` wall event — this
    is how Phase 3's RAG answers light up the wall's Citations pane.
    """
    if not USE_REAL_DDB or not text:
        return
    try:
        meta = ddb.get_call_meta(call_id)
        seq = int(meta.get("turnSeq") or 0) + 1
        ddb.append_turn(
            call_id,
            seq,
            "Agent",
            text,
            company_id=meta.get("companyId") or company_id,
            citations=citations or None,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("agent turn log failed: %s", exc)


def _log_caller_turn(call_id: str, company_id: str, text: str) -> None:
    if not USE_REAL_DDB or not text:
        return
    try:
        meta = ddb.get_call_meta(call_id)
        seq = int(meta.get("turnSeq") or 0) + 1
        ddb.append_turn(
            call_id, seq, "Caller", text,
            company_id=meta.get("companyId") or company_id,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("caller turn log failed: %s", exc)


# ---------------------------------------------------------------------------
# Phase 3 RAG helpers
# ---------------------------------------------------------------------------

import re as _re_q

_QUESTION_VOCAB = {
    "how much", "how many", "how long", "price", "cost", "rate",
    "include", "covered", "cover", "policy", "weekend", "evening",
    "hours", "guarantee", "cancel", "photos", "needed", "service",
    "do you", "can you", "does it", "is it", "are you",
    # "Statement-of-intent-to-ask" patterns — caller signals they have a
    # question before asking it.
    "question", "ask",
}
_QUESTION_FIRST_TOKENS = {
    "how", "what", "when", "where", "why", "who",
    "do", "does", "did", "can", "could", "will", "would", "should",
    "is", "are", "was", "were",
}
# Filler stutters ASR commonly inserts before the real utterance.
_FILLER_PREFIXES = {
    "i'm", "im", "uh", "um", "err", "well", "so", "like", "okay", "ok",
    "yeah", "yes", "no",
}


def _is_question(text: str) -> bool:
    """Heuristic: distinguish FAQ-style queries from slot answers.

    Robust to ASR filler prefixes like "i'm do you clean offices on the
    weekend" — strip a small set of well-known fillers from the start
    before checking the question vocabulary.
    """
    if not text:
        return False
    s = text.strip()
    if s.endswith("?"):
        return True
    tokens = [t.rstrip(".,!?") for t in s.lower().split()]
    if not tokens:
        return False
    # Drop leading fillers ("i'm", "uh", "okay") so the real question token
    # ("do you ...") becomes the first.
    while tokens and tokens[0] in _FILLER_PREFIXES:
        tokens.pop(0)
    if not tokens:
        return False
    first = tokens[0]
    lowered = " ".join(tokens)
    # Strong signal: any question vocab anywhere in the utterance.
    if any(v in lowered for v in _QUESTION_VOCAB):
        return True
    if first not in _QUESTION_FIRST_TOKENS:
        return False
    # Generic safety net: longer WH-led utterances are usually questions.
    return len(tokens) >= 4


def _just_elicited_slot(lex_slots: dict[str, Any], transcript: str) -> str | None:
    """Find the slot whose originalValue equals the caller's transcript — that's
    what Lex was eliciting this turn."""
    needle = (transcript or "").strip()
    for slot, payload in lex_slots.items():
        if not isinstance(payload, dict):
            continue
        raw = ((payload.get("value") or {}).get("originalValue") or "").strip()
        if raw == needle:
            return slot
    return None


def _compose_faq_answer(
    kb_result: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Turn a KB lookup result into a spoken answer + citations.

    Refusal policy: if KB has no entries or the top score is below
    `ANSWER_MIN_SCORE`, return a refusal and NO citations — the wall stays
    clean and the agent doesn't invent.
    """
    status = kb_result.get("status")
    top_score = int(kb_result.get("top_score") or 0)
    citations = kb_result.get("citations") or []
    if status != "ok" or top_score < ANSWER_MIN_SCORE or not citations:
        return ("I don't have that information.", [])
    top = citations[0]
    body = (top.get("excerpt") or "").rstrip(". ").strip()
    if not body:
        return ("I don't have that information.", [])
    # Lead with the topic so the spoken answer feels like an answer ("Window
    # cleaning. Interior, frames, sills…"), not just a continuation.
    title = _extract_title_from_source(top.get("source", ""))
    if title and title.lower() not in body.lower()[:60]:
        return (f"{title}. {body}.", citations[:2])
    return (f"{body}.", citations[:2])


def _extract_title_from_source(source: str) -> str:
    # `_citation_source` format is "CATEGORY · Title"; strip the category prefix.
    parts = source.split("·", 1)
    return parts[-1].strip()


def _handle_faq_turn(
    *,
    transcript: str,
    event: dict[str, Any],
    intent: dict[str, Any],
    lex_slots: dict[str, Any],
    attrs: dict[str, str],
    call_id: str,
    booking_id: str,
    company_id: str,
    final: bool = False,
) -> dict[str, Any]:
    """Answer a caller question from KB.

    `final=False` (default) — re-elicit the slot Lex was asking about so the
    booking flow resumes. Used when caller interrupts mid-elicitation.
    `final=True` — the call is already past slot collection (the post-slot
    "any questions?" beat); answer + close + speak the price summary.
    """
    # Don't let Lex's auto-capture of the question pollute the slot.
    elicited = _just_elicited_slot(lex_slots, transcript)
    if elicited:
        lex_slots.pop(elicited, None)

    _log_caller_turn(call_id, company_id, transcript)

    kb_result = kb_lookup(transcript, company_id, top_k=3)
    answer, citations = _compose_faq_answer(kb_result)

    state = _state_from_lex_slots(lex_slots)
    attrs["callId"] = call_id
    attrs["bookingId"] = booking_id
    attrs["companyId"] = company_id

    if final:
        # We're in the "any questions?" phase — answer + invite another
        # question. Only close when the caller has nothing more (handled in
        # `_finalize_after_questions`). This lets the caller keep asking
        # follow-ups instead of being hung up on after one answer.
        followup = "Is there anything else I can help with?"
        response_text = f"{answer} {followup}"
        _log_agent_turn(call_id, company_id, response_text, citations=citations)
        return lex_v2.elicit_slot(
            "location", response_text,
            session_attributes=attrs,
            intent_name=intent.get("name", lex_v2.INTENT_NAME),
            intent_slots=lex_slots,
        )

    next_slot = next(
        (s for s in lex_v2.REQUIRED_SLOT_ORDER if not _slot_filled(state, s)),
        None,
    )
    slot_to_elicit = elicited or next_slot

    if slot_to_elicit and slot_to_elicit in lex_v2.PROMPTS:
        response_text = f"{answer} {lex_v2.PROMPTS[slot_to_elicit]}"
    else:
        response_text = answer

    _log_agent_turn(call_id, company_id, response_text, citations=citations)

    if slot_to_elicit:
        return lex_v2.elicit_slot(
            slot_to_elicit, response_text,
            session_attributes=attrs,
            intent_name=intent.get("name", lex_v2.INTENT_NAME),
            intent_slots=lex_slots,
        )
    return lex_v2.close_session(
        response_text,
        session_attributes=attrs,
        intent_name=intent.get("name", lex_v2.INTENT_NAME),
    )


def _react_to_estimate(
    *,
    transcript: str,
    event: dict[str, Any],
    intent: dict[str, Any],
    lex_slots: dict[str, Any],
    attrs: dict[str, str],
    call_id: str,
    booking_id: str,
    company_id: str,
) -> dict[str, Any]:
    """Caller's first turn after we spoke the estimate.

    Their reply is one of:
      - a quick reaction ("okay", "sounds good")
      - a question about the price/service (→ KB answer)
      - the location directly ("Bahnhofstrasse 23, Zurich") if they're
        eager to keep moving

    For (a) and (b) we pop the location-transport slot so junk text doesn't
    pollute the real location. For (c) we KEEP the capture, save it, and
    skip the asking_location beat.
    """
    elicited = _just_elicited_slot(lex_slots, transcript) if transcript else None
    if elicited:
        payload = lex_slots.get(elicited) or {}
        raw = (
            (payload.get("value") or {}).get("interpretedValue")
            or (payload.get("value") or {}).get("originalValue")
            or ""
        ).strip()
        # Eager-path: caller gave a real location instead of just reacting.
        if elicited == "location" and raw and _looks_like_location(raw):
            save_slot_adapter(
                call_id=call_id, booking_id=booking_id,
                slot="location", value=raw,
            )
            # Leave it in lex_slots so state reflects it below.
        else:
            lex_slots.pop(elicited, None)

    if transcript:
        _log_caller_turn(call_id, company_id, transcript)

    state = _state_from_lex_slots(lex_slots)

    citations: list[dict[str, Any]] = []
    answer_prefix = ""
    if transcript and _is_question(transcript):
        kb_result = kb_lookup(transcript, company_id, top_k=3)
        answer, citations = _compose_faq_answer(kb_result)
        answer_prefix = f"{answer} "

    # Skip the redundant location ask if the caller already gave one during
    # slot collection — go straight to the closing "any questions?" beat.
    if _slot_filled(state, "location"):
        attrs["phase"] = "any_questions"
        prompt = (
            f"{answer_prefix}Before we wrap up — do you have any questions "
            "about our services, pricing, or scheduling?"
        )
    else:
        attrs["phase"] = "asking_location"
        prompt = (
            f"{answer_prefix}What's the address or area where the cleaning "
            "will take place?"
        )

    attrs["callId"] = call_id
    attrs["bookingId"] = booking_id
    attrs["companyId"] = company_id
    _log_agent_turn(
        call_id, company_id, prompt,
        citations=citations or None,
    )
    return lex_v2.elicit_slot(
        "location", prompt,
        session_attributes=attrs,
        intent_name=intent.get("name", lex_v2.INTENT_NAME),
        intent_slots=lex_slots,
    )


def _finalize_after_questions(
    *,
    transcript: str,
    event: dict[str, Any],
    intent: dict[str, Any],
    lex_slots: dict[str, Any],
    attrs: dict[str, str],
    call_id: str,
    booking_id: str,
    company_id: str,
) -> dict[str, Any]:
    """Close the call after the caller declined to ask anything.

    Don't run slot cleaning — Lex auto-captured the "no thanks" reply against
    the transport slot (location), but the real location was already saved
    earlier. Just log the caller turn, speak the price summary, end the call.
    """
    elicited = _just_elicited_slot(lex_slots, transcript)
    if elicited:
        # Restore the slot to its previously-captured value. Easiest: just
        # drop the new capture from Lex's view.
        lex_slots.pop(elicited, None)

    if transcript:
        _log_caller_turn(call_id, company_id, transcript)

    state = _state_from_lex_slots(lex_slots)
    close_message = _compose_close_message(booking_id, state)
    _log_agent_turn(call_id, company_id, close_message)
    if USE_REAL_DDB:
        try:
            ddb.end_call(call_id, booking_id, reason="completed")
        except Exception as exc:  # pragma: no cover
            logger.warning("end_call failed: %s", exc)

    attrs["callId"] = call_id
    attrs["bookingId"] = booking_id
    attrs["companyId"] = company_id
    return lex_v2.close_session(
        close_message,
        session_attributes=attrs,
        intent_name=intent.get("name", lex_v2.INTENT_NAME),
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


_SERVICE_TYPES = {
    "MOVE_OUT_CLEANING", "OFFICE_CLEANING", "WINDOW_CLEANING",
    "CONSTRUCTION_CLEANING", "FACILITY_MAINTENANCE",
}
_URGENCY_VALUES = {"low", "medium", "high", "urgent"}


def _validate_slot_value(slot: str, value: Any) -> Any | None:
    """Return the value if it's plausible for this slot, else None.

    The handler uses this to reject obviously-wrong captures (e.g.
    `what="i'm sorry"`, `rooms="oh oh"`) so the caller gets re-prompted
    instead of saving garbage to the booking.
    """
    if value is None or value == "":
        return None
    s = str(value).strip()
    if not s:
        return None
    if slot == "what":
        normalized = s.upper().replace("-", "_").replace(" ", "_")
        return s if normalized in _SERVICE_TYPES else None
    if slot == "rooms":
        try:
            n = float(s)
        except (TypeError, ValueError):
            return None
        if n <= 0:
            return None
        return value if isinstance(value, (int, float)) else (int(n) if n.is_integer() else n)
    if slot == "urgency":
        return s.lower() if s.lower() in _URGENCY_VALUES else None
    if slot == "area":
        import re as _re
        return s if _re.search(r"\d", s) else None
    if slot == "when":
        return s if 0 < len(s) <= 100 else None
    if slot == "location":
        # Reject obvious non-locations ("no thanks", "i have a question") via
        # the same loose plausibility check the asking_location phase uses.
        return s if _looks_like_location(s) else None
    return None


def _clean_lex_slots(lex_slots: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    """Canonicalize each Lex auto-captured slot AND validate it.

    For every slot:
      1. Run the regex extractor on the raw text; if it finds a canonical
         value (e.g. "office" → "OFFICE_CLEANING"), use that.
      2. Otherwise try `_bare_number_fallback` for area/rooms/when.
      3. Validate via `_validate_slot_value`. If invalid (e.g. `what` =
         "i'm sorry"), POP the slot from `lex_slots` — that way Lex
         re-elicits it instead of accepting garbage.

    Returns the cleaned dict AND a list of `(slot, value)` pairs that the
    handler should persist via the adapter.
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
        if canonical is None:
            canonical = _bare_number_fallback(slot, raw)
        # Validate (the canonical or raw, whichever we ended up with).
        candidate = canonical if canonical is not None else raw
        validated = _validate_slot_value(slot, candidate)
        if validated is None:
            # Drop the slot entirely so Lex re-elicits.
            continue
        if str(validated) != raw:
            cleaned[slot] = _to_lex_slot_value(validated)
            changed.append((slot, validated))
        else:
            cleaned[slot] = payload
    return cleaned, changed


def _bare_number_fallback(slot: str, raw: str) -> Any | None:
    """Per-slot fallback when the regex extractor finds nothing canonical.

    - area / rooms: accept a digit or word-number (Lex was eliciting → caller's
      bare reply belongs to that slot).
    - when: accept any short text — calendar phrases like "in three days",
      "next monday morning" don't match our regex but are still meaningful for
      the wall and the operator. Cap at 60 chars to avoid swallowing FAQs the
      question detector missed.

    All slots also strip a leading lone letter — ASR commonly prepends "a "
    before the real utterance ("a five", "a in three days").
    """
    import re as _re
    raw = _re.sub(r"^\s*[a-zA-Z]\.?\s+", "", raw).strip()
    if slot in {"area", "rooms"}:
        text = raw.rstrip(".").lower()
        m = _re.match(r"^\s*(\d+(?:\.\d+)?)\s*$", text)
        if m:
            n = float(m.group(1))
            if slot == "area":
                return f"{n:g} m2"
            return int(n) if n.is_integer() else n
        from slot_extraction import _parse_word_number  # local import to avoid cycle
        n = _parse_word_number(text)
        if n is None:
            return None
        if slot == "area":
            return f"{n} m2"
        return n
    if slot in {"when", "location"}:
        cleaned = raw.rstrip(".").strip()
        max_len = 60 if slot == "when" else 100
        if 0 < len(cleaned) <= max_len:
            return cleaned
    return None


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


_REACTION_PHRASES = {
    "yes", "no", "ok", "okay", "sure", "alright", "all right",
    "fine", "good", "great", "perfect", "thanks", "thank you",
    "sounds good", "that works", "no thanks", "yes please",
    "yep", "nope", "yeah", "nah", "uh huh", "mhm",
}

_AFFIRMATIVE_PHRASES = {
    "yes", "yeah", "yep", "yup", "sure", "i do", "yes please",
    "of course", "absolutely", "ok yes", "yes i do", "i have a question",
    "i have one",
}


def _is_affirmative(text: str) -> bool:
    """True if the caller said something that means 'yes, I have a question'.

    Used in the any_questions phase: a bare 'yes' shouldn't close the call —
    the caller is signaling they want to ask something but haven't yet.
    """
    if not text:
        return False
    lowered = text.strip().lower().rstrip(".,!?")
    return lowered in _AFFIRMATIVE_PHRASES


def _looks_like_location(text: str) -> bool:
    """Loose plausibility check for a spoken address / area.

    Heuristic: at least 3 characters, contains an alpha, NOT a question,
    NOT in the well-known reaction phrase list, and either contains a
    digit (street number / PLZ) or has at least two words. Rejects:
      - "i have another question" (question)
      - "sounds good" / "no thanks" (reactions, not addresses)
      - "ok" (too short to identify a place)
    Accepts:
      - "Bahnhofstrasse 23, Zurich" (digit)
      - "Zurich Altstadt" (≥2 words)
      - "8001 Zurich" (digit + word)
    """
    if not text:
        return False
    s = text.strip()
    if len(s) < 3:
        return False
    if not any(c.isalpha() for c in s):
        return False
    if _is_question(s):
        return False
    lowered = s.lower().rstrip(".,!?")
    if lowered in _REACTION_PHRASES:
        return False
    has_digit = any(c.isdigit() for c in s)
    word_count = len(s.split())
    if not has_digit and word_count < 2:
        return False
    return True


def _derive_urgency_from_when(when_text: str) -> str | None:
    """Infer urgency from the spoken `when` so we don't ask redundantly.

    Returns None for ambiguous phrases — handler will fall back to eliciting
    `urgency` explicitly only when this can't decide.
    """
    if not when_text:
        return None
    import re as _re
    lowered = when_text.lower().strip()
    if any(k in lowered for k in (
        "today", "asap", "right now", "right away",
        "urgent", "emergency", "immediately", "as soon",
    )):
        return "high"
    if "tomorrow" in lowered:
        return "high"
    # "in N days" — small N is high, larger is medium.
    word_to_int = {"one":1, "two":2, "three":3, "four":4, "five":5,
                   "six":6, "seven":7, "eight":8, "nine":9, "ten":10}
    m = _re.search(r"in\s+(\d+|" + "|".join(word_to_int) + r")\s+days?", lowered)
    if m:
        token = m.group(1)
        n = word_to_int.get(token) or (int(token) if token.isdigit() else 0)
        if n and n <= 2:
            return "high"
        if n and n <= 7:
            return "medium"
        if n:
            return "low"
    if "this week" in lowered:
        return "high"
    if "next week" in lowered or "next month" in lowered:
        return "medium"
    if any(k in lowered for k in ("flexible", "whenever", "no rush", "any time", "anytime")):
        return "low"
    # Day-of-week mentioned without other qualifiers — assume medium.
    weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    if any(d in lowered for d in weekdays):
        return "medium"
    # Default for any non-empty when phrase the caller bothered to specify:
    # treat as medium rather than asking redundantly.
    if len(lowered) >= 3:
        return "medium"
    return None


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


def _compose_estimate_speech(booking_id: str, state: SlotState) -> str:
    """Speak the price + feasibility note (no goodbye).

    Used at the end of the `collecting` phase to read the Brain quote back
    to the caller before we ask for their address.
    """
    fallback = "Got everything I need to put together an estimate."
    if not USE_REAL_DDB:
        return fallback
    try:
        booking = ddb.get_booking(booking_id) or {}
    except Exception as exc:  # pragma: no cover
        logger.warning("estimate: get_booking failed: %s", exc)
        return fallback

    brain = booking.get("brain") or {}
    price = brain.get("price")
    if price is None:
        return fallback

    service = _SERVICE_SPOKEN.get(brain.get("serviceType", ""), "your cleaning")
    currency = _CURRENCY_SPOKEN.get(
        str(brain.get("currency", "")), str(brain.get("currency", ""))
    )
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
            parts.append("We may ask for a couple of photos before we confirm.")
        elif "no_crew_assigned" in reasons:
            parts.append("Someone from our team will follow up to confirm a crew.")
        elif "large_area" in reasons or "large_rooms" in reasons or "over_capacity" in reasons:
            parts.append("Because of the size, our team will confirm the final quote.")
        else:
            parts.append("Our team will follow up to confirm the booking.")
    elif status == "unsupported":
        return "I'm sorry, that service isn't one we offer right now. Our team will be in touch."
    return " ".join(parts)


def _compose_close_message(booking_id: str, state: SlotState) -> str:
    """Goodbye line — price was already spoken in the estimate phase."""
    return "We'll be in touch shortly to confirm. Thanks for calling Glanz AG. Goodbye!"


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
        "location": state.location,
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
    # EventBridge cron pings (see lambda_stack.py) keep the execution
    # environment warm so first-turn latency isn't dominated by cold-start.
    # Short-circuit before any heavy work.
    if isinstance(event, dict) and event.get("warmer"):
        return {"status": "warm"}

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
