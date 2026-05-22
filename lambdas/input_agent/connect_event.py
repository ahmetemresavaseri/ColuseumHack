"""Amazon Connect contact-flow Lambda helpers.

Connect's `InvokeLambdaFunction` block sends an event like:

    {
      "Details": {
        "ContactData": {
          "ContactId": "abc",
          "InstanceARN": "...",
          "CustomerEndpoint": {"Address": "+1...", "Type": "TELEPHONE_NUMBER"},
          "SystemEndpoint":   {"Address": "+1...", "Type": "TELEPHONE_NUMBER"},
          "Attributes": {"companyId": "glanz-ag", ...}
        },
        "Parameters": {}
      },
      "Name": "ContactFlowEvent"
    }

It expects a flat string-only JSON object back; values are accessible in the
flow as `$.External.<key>`.

We use this Lambda invocation for **session bootstrap only**: it creates the
Calls/Bookings records, resolves the tenant from the dialed number, and hands
back the greeting + IDs which the contact flow then forwards into Lex via
session attributes on the `GetCustomerInput` block.
"""
from __future__ import annotations

from typing import Any


def is_connect_event(event: dict[str, Any]) -> bool:
    return (
        isinstance(event, dict)
        and "Details" in event
        and isinstance(event["Details"], dict)
        and "ContactData" in event["Details"]
    )


def get_contact_data(event: dict[str, Any]) -> dict[str, Any]:
    return event.get("Details", {}).get("ContactData", {}) or {}


def get_contact_id(event: dict[str, Any]) -> str:
    return get_contact_data(event).get("ContactId", "")


def get_caller_phone(event: dict[str, Any]) -> str:
    endpoint = get_contact_data(event).get("CustomerEndpoint") or {}
    return endpoint.get("Address", "")


def get_dialed_phone(event: dict[str, Any]) -> str:
    endpoint = get_contact_data(event).get("SystemEndpoint") or {}
    return endpoint.get("Address", "")


def get_attribute(event: dict[str, Any], name: str, default: str = "") -> str:
    return get_contact_data(event).get("Attributes", {}).get(name, default)


def respond(payload: dict[str, Any]) -> dict[str, str]:
    """Flatten + stringify a contact-flow response.

    Connect only accepts string values; nested dicts / non-string types are
    silently dropped by the flow engine. Convert everything up front.
    """
    return {k: str(v) for k, v in payload.items() if v is not None}
