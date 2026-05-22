from __future__ import annotations

import slot_adapter
from tool_dispatcher import dispatch_tool


def setup_function(_func):
    slot_adapter.reset()


def test_save_slot_routes_through_adapter():
    result = dispatch_tool(
        "save_slot",
        {
            "callId": "c1",
            "bookingId": "b1",
            "slot": "what",
            "value": "MOVE_OUT_CLEANING",
        },
    )
    assert result["status"] == "saved"
    assert result["backend"] == "mock"
    assert result["slot"] == "what"


def test_kb_lookup_safe_fallback(monkeypatch):
    monkeypatch.delenv("BEDROCK_KB_ID", raising=False)
    result = dispatch_tool(
        "kb_lookup",
        {"question": "How much?", "companyId": "glanz-ag"},
    )
    assert result["status"] == "kb_not_configured"
    assert result["citations"] == []


def test_compute_price_returns_stub_without_brain(monkeypatch):
    monkeypatch.delenv("BRAIN_FUNCTION_NAME", raising=False)
    result = dispatch_tool("compute_price", {"slots": {"what": "X"}})
    assert result["status"] == "brain_not_configured"


def test_end_call_emits_clean_payload():
    result = dispatch_tool("end_call", {"callId": "c1", "reason": "user_hangup"})
    assert result == {"status": "ended", "callId": "c1", "reason": "user_hangup"}


def test_unknown_tool_returns_error():
    assert dispatch_tool("not_a_tool", {})["error"].startswith("unknown_tool")


def test_feasibility_assessment_tool_bookable():
    result = dispatch_tool(
        "feasibility_assessment",
        {
            "slots": {"what": "OFFICE_CLEANING", "area": 50, "rooms": 4},
            "brain": {
                "status": "estimated",
                "serviceType": "OFFICE_CLEANING",
                "needsPhotos": False,
                "crew": {"crewId": "c1", "capacityHoursPerDay": 8},
            },
            "crews": [{"crewId": "c1"}],
        },
    )
    assert result["status"] == "bookable"


def test_feasibility_assessment_tool_unsupported():
    result = dispatch_tool(
        "feasibility_assessment",
        {"slots": {}, "brain": {"status": "needs_service_type"}, "crews": []},
    )
    assert result["status"] == "unsupported"
