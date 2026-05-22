"""Amazon Lex V2 CodeHook fulfillment for the Atrium voice agent.

Lex invokes this Lambda on every caller utterance (we wire it as the dialogCodeHook
and fulfillmentCodeHook of a single FallbackIntent on a bot whose nluConfidenceThreshold
is high enough that almost everything falls through).

Event shape:
    {
      "sessionId": "...",          # we reuse this as our callId
      "inputTranscript": "...",    # caller's speech, ASR'd by Lex
      "sessionState": {
        "sessionAttributes": {
          "companyId": "glanz-ag", # injected from the Connect contact flow
          "bookingId": "bk_...",   # generated on first turn
          "messages": "[...json...]",  # serialized Bedrock Converse history
          "ended": "false"
        },
        "intent": {"name": "FallbackIntent", "state": "InProgress"}
      },
      ...
    }

Response shape (Close = end this Lex turn, ConfirmIntent/ElicitIntent = continue):
    {
      "sessionState": {
        "sessionAttributes": {...},
        "dialogAction": {"type": "ElicitIntent"} | {"type": "Close"},
        "intent": {"name": "FallbackIntent", "state": "Fulfilled"}
      },
      "messages": [{"contentType": "PlainText", "content": "<spoken text>"}]
    }
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from brain_client import messages_from_session, messages_to_session, take_turn
from ddb import get_company
from prompts import persona_prompt

logger = logging.getLogger(__name__)


def handle_lex(event: dict[str, Any]) -> dict[str, Any]:
    session_state = event.get("sessionState") or {}
    attrs = dict(session_state.get("sessionAttributes") or {})
    intent = session_state.get("intent") or {"name": "FallbackIntent"}

    call_id = event.get("sessionId") or attrs.get("callId") or str(uuid.uuid4())
    company_id = attrs.get("companyId") or "glanz-ag"
    booking_id = attrs.get("bookingId") or f"bk_{uuid.uuid4().hex[:12]}"
    user_text = (event.get("inputTranscript") or "").strip()

    attrs["callId"] = call_id
    attrs["companyId"] = company_id
    attrs["bookingId"] = booking_id

    company = get_company(company_id)
    system_prompt = persona_prompt(company)
    messages = messages_from_session(attrs.get("messages"))

    logger.info(
        "LEX_TURN callId=%s companyId=%s text_len=%d prior_msgs=%d",
        call_id, company_id, len(user_text), len(messages),
    )

    reply, updated_messages, tool_trace = take_turn(
        user_text=user_text,
        messages=messages,
        system_prompt=system_prompt,
        call_id=call_id,
        company_id=company_id,
        booking_id=booking_id,
    )

    ended = any(t["name"] == "end_call" for t in tool_trace)
    attrs["messages"] = messages_to_session(updated_messages)
    attrs["ended"] = "true" if ended else "false"

    if not reply:
        reply = "Sorry, could you repeat that?"

    dialog_action = {"type": "Close"} if ended else {"type": "ElicitIntent"}
    intent_state = "Fulfilled" if ended else "InProgress"

    return {
        "sessionState": {
            "sessionAttributes": attrs,
            "dialogAction": dialog_action,
            "intent": {"name": intent.get("name", "FallbackIntent"), "state": intent_state},
        },
        "messages": [{"contentType": "PlainText", "content": reply}],
    }
