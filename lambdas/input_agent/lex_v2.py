"""Lex V2 fulfillment helpers.

Lex V2 invokes our Lambda for two purposes:
  - **DialogCodeHook** between slot-elicitation turns (we extract slots from
    `inputTranscript`, write them to DynamoDB, and tell Lex which slot to
    elicit next).
  - **FulfillmentCodeHook** when the intent is `ReadyForFulfillment` (we mark
    the call ended, optionally invoke the Brain, and tell Lex to close the
    session).

Event/response shapes:
  https://docs.aws.amazon.com/lexv2/latest/dg/lambda-input-format.html
  https://docs.aws.amazon.com/lexv2/latest/dg/lambda-response-format.html
"""
from __future__ import annotations

from typing import Any

INTENT_NAME = "CollectBooking"
REQUIRED_SLOT_ORDER = ("when", "what", "area", "rooms", "urgency", "email")

PROMPTS: dict[str, str] = {
    "when": "When would you like the cleaning?",
    "what": "What kind of cleaning do you need — move-out, office, window, or construction?",
    "area": "How many square meters or square feet is the space?",
    "rooms": "How many rooms?",
    "urgency": "How urgent is this — same day, this week, or flexible?",
    "email": "What email address should I send the confirmation to?",
}


def is_lex_event(event: dict[str, Any]) -> bool:
    return "sessionState" in event and "inputTranscript" in event


def get_session_attributes(event: dict[str, Any]) -> dict[str, str]:
    return dict(event.get("sessionState", {}).get("sessionAttributes", {}) or {})


def get_input_transcript(event: dict[str, Any]) -> str:
    return event.get("inputTranscript", "") or ""


def get_invocation_source(event: dict[str, Any]) -> str:
    return event.get("invocationSource", "DialogCodeHook")


def get_intent_name(event: dict[str, Any]) -> str:
    return (
        event.get("sessionState", {})
        .get("intent", {})
        .get("name", INTENT_NAME)
    )


def elicit_slot(
    slot: str,
    message: str,
    *,
    session_attributes: dict[str, str],
    intent_name: str = INTENT_NAME,
    intent_slots: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "sessionState": {
            "sessionAttributes": session_attributes,
            "dialogAction": {"type": "ElicitSlot", "slotToElicit": slot},
            "intent": {
                "name": intent_name,
                "slots": intent_slots or {},
                "state": "InProgress",
            },
        },
        "messages": [{"contentType": "PlainText", "content": message}],
    }


def close_session(
    message: str,
    *,
    session_attributes: dict[str, str],
    intent_name: str = INTENT_NAME,
    fulfillment_state: str = "Fulfilled",
) -> dict[str, Any]:
    return {
        "sessionState": {
            "sessionAttributes": session_attributes,
            "dialogAction": {"type": "Close"},
            "intent": {
                "name": intent_name,
                "state": fulfillment_state,
            },
        },
        "messages": [{"contentType": "PlainText", "content": message}],
    }


def delegate(*, session_attributes: dict[str, str], intent_name: str = INTENT_NAME) -> dict[str, Any]:
    return {
        "sessionState": {
            "sessionAttributes": session_attributes,
            "dialogAction": {"type": "Delegate"},
            "intent": {"name": intent_name, "state": "InProgress"},
        }
    }
