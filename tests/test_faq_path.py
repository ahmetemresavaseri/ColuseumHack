from __future__ import annotations

import json
from pathlib import Path

import handler
import kb as kb_module
import slot_adapter
from handler import (
    _compose_faq_answer,
    _handle_faq_turn,
    _is_question,
    _just_elicited_slot,
)
from session import reset_all


SEED = json.loads(
    (Path(__file__).resolve().parent.parent / "infrastructure/seed/knowledge_items.json").read_text()
)


def _stub_kb(monkeypatch):
    class FakeTable:
        def query(self, **kwargs):
            return {"Items": [i for i in SEED if i["companyId"] == "glanz-ag"]}
    class FakeResource:
        def Table(self, _name):
            return FakeTable()
    monkeypatch.setattr(kb_module, "boto3", type("X", (), {"resource": lambda self, _: FakeResource()})())
    monkeypatch.setenv("KNOWLEDGE_ITEMS_TABLE", "atrium-knowledgeitems")


def setup_function(_func):
    reset_all()
    slot_adapter.reset()


def test_is_question_positive_cases():
    assert _is_question("How much is window cleaning per square meter?")
    assert _is_question("Do you also wash cars?")
    assert _is_question("Can you do my taxes?")
    assert _is_question("What is included in office cleaning?")
    assert _is_question("Is there a weekend surcharge?")


def test_is_question_negative_cases():
    # These are slot answers, not questions.
    assert not _is_question("tomorrow")
    assert not _is_question("office")
    assert not _is_question("a. b. c. at gmail dot com")
    assert not _is_question("85 square meters")
    assert not _is_question("five rooms")
    assert not _is_question("urgent please")


def test_just_elicited_slot_finds_match():
    lex_slots = {
        "area": {"value": {"originalValue": "How much per m2?", "interpretedValue": "How much per m2?"}},
        "when": {"value": {"originalValue": "tomorrow", "interpretedValue": "tomorrow"}},
    }
    assert _just_elicited_slot(lex_slots, "How much per m2?") == "area"
    assert _just_elicited_slot(lex_slots, "tomorrow") == "when"
    assert _just_elicited_slot(lex_slots, "something else") is None


def test_compose_faq_answer_in_scope(monkeypatch):
    _stub_kb(monkeypatch)
    result = kb_module.kb_lookup("Do you clean offices on weekends?", "glanz-ag")
    answer, citations = _compose_faq_answer(result)
    assert answer != "I don't have that information."
    assert citations
    assert "weekend" in answer.lower() or "evening" in answer.lower()


def test_compose_faq_answer_refuses_out_of_scope(monkeypatch):
    _stub_kb(monkeypatch)
    result = kb_module.kb_lookup("Do you also wash cars?", "glanz-ag")
    answer, citations = _compose_faq_answer(result)
    assert answer == "I don't have that information."
    assert citations == []


def test_handle_faq_turn_returns_elicit_with_answer(monkeypatch):
    monkeypatch.delenv("DDB_BACKEND", raising=False)  # mock mode
    _stub_kb(monkeypatch)
    lex_slots = {
        "area": {"value": {"originalValue": "How much is window cleaning?", "interpretedValue": "How much is window cleaning?"}},
    }
    response = _handle_faq_turn(
        transcript="How much is window cleaning?",
        event={"sessionState": {"intent": {"name": "CollectBooking"}}},
        intent={"name": "CollectBooking"},
        lex_slots=lex_slots,
        attrs={},
        call_id="c1", booking_id="b1", company_id="glanz-ag",
    )
    state = response["sessionState"]
    # Lambda re-elicits the slot Lex was eliciting (area).
    assert state["dialogAction"]["type"] == "ElicitSlot"
    assert state["dialogAction"]["slotToElicit"] == "area"
    # Bad capture removed; Lex won't store "How much is window cleaning?" as area.
    assert "area" not in state["intent"]["slots"]
    # Response includes the KB answer + restated prompt.
    spoken = response["messages"][0]["content"]
    assert "square meter" in spoken.lower() or "5" in spoken
    assert "how many square" in spoken.lower()  # re-prompt for area


def test_handle_faq_turn_refuses_unknown(monkeypatch):
    monkeypatch.delenv("DDB_BACKEND", raising=False)
    _stub_kb(monkeypatch)
    lex_slots = {
        "when": {"value": {"originalValue": "Do you also wash cars?", "interpretedValue": "Do you also wash cars?"}},
    }
    response = _handle_faq_turn(
        transcript="Do you also wash cars?",
        event={"sessionState": {"intent": {"name": "CollectBooking"}}},
        intent={"name": "CollectBooking"},
        lex_slots=lex_slots,
        attrs={},
        call_id="c1", booking_id="b1", company_id="glanz-ag",
    )
    spoken = response["messages"][0]["content"]
    assert "I don't have that information" in spoken
    # Still re-elicits the slot Lex was asking about (when).
    assert response["sessionState"]["dialogAction"]["slotToElicit"] == "when"
