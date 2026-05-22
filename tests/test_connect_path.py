from __future__ import annotations

import os

import slot_adapter
from handler import handle_connect_event
from session import reset_all


def setup_function(_func):
    reset_all()
    slot_adapter.reset()
    os.environ.pop("DDB_BACKEND", None)


def _connect_event(**overrides) -> dict:
    base = {
        "Details": {
            "ContactData": {
                "ContactId": "contact-abc",
                "CustomerEndpoint": {
                    "Address": "+15551234567",
                    "Type": "TELEPHONE_NUMBER",
                },
                "SystemEndpoint": {
                    "Address": "+15557654321",
                    "Type": "TELEPHONE_NUMBER",
                },
                "Attributes": {"companyId": "glanz-ag"},
            },
            "Parameters": {},
        },
        "Name": "ContactFlowEvent",
    }
    base["Details"]["ContactData"].update(overrides)
    return base


def test_connect_event_returns_flat_string_attributes():
    response = handle_connect_event(_connect_event())
    # Connect requires *string* values in the response — assert that strictly.
    assert all(isinstance(v, str) for v in response.values())
    assert response["callId"] == "contact-abc"
    assert response["bookingId"].startswith("booking-")
    assert response["companyId"] == "glanz-ag"
    assert "Hello" in response["greeting"]


def test_connect_event_default_companyId_when_attribute_missing():
    event = _connect_event()
    event["Details"]["ContactData"]["Attributes"] = {}
    response = handle_connect_event(event)
    assert response["companyId"] in {"demo-tenant", "glanz-ag"}
    assert response["companyId"]
