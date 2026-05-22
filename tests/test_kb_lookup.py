from __future__ import annotations

import json
from pathlib import Path

import kb as kb_module


SEED = json.loads(
    (Path(__file__).resolve().parent.parent / "infrastructure/seed/knowledge_items.json").read_text()
)


def _local_kb(monkeypatch):
    """Patch boto3 out of the KB lookup so tests don't need AWS."""
    class FakeTable:
        def query(self, **kwargs):
            return {"Items": [i for i in SEED if i["companyId"] == "glanz-ag"]}

    class FakeResource:
        def Table(self, _name):
            return FakeTable()

    monkeypatch.setattr(kb_module, "boto3", type("X", (), {"resource": lambda self, _: FakeResource()})())
    monkeypatch.setenv("KNOWLEDGE_ITEMS_TABLE", "atrium-knowledgeitems")


def test_scores_above_threshold_for_in_scope_question(monkeypatch):
    _local_kb(monkeypatch)
    result = kb_module.kb_lookup("Do you clean offices on weekends?", "glanz-ag", top_k=3)
    assert result["status"] == "ok"
    assert result["top_score"] >= kb_module.ANSWER_MIN_SCORE
    top = result["citations"][0]
    assert top["itemId"] == "faq#office-hours"
    assert "evening" in top["excerpt"] or "weekend" in top["excerpt"]


def test_pricing_query_prefers_pricing_item(monkeypatch):
    _local_kb(monkeypatch)
    result = kb_module.kb_lookup("How much per square meter for window cleaning?", "glanz-ag")
    assert result["citations"][0]["itemId"] == "pricing#summary"


def test_out_of_scope_returns_no_match(monkeypatch):
    _local_kb(monkeypatch)
    result = kb_module.kb_lookup("Do you also wash cars?", "glanz-ag")
    # Either no match at all OR top score below the answer threshold.
    if result["status"] == "ok":
        assert result["top_score"] < kb_module.ANSWER_MIN_SCORE
    else:
        assert result["status"] == "kb_no_match"


def test_empty_question_returns_some_items(monkeypatch):
    _local_kb(monkeypatch)
    result = kb_module.kb_lookup("", "glanz-ag", top_k=2)
    assert len(result["citations"]) == 2


def test_unknown_company_empty(monkeypatch):
    class FakeTableEmpty:
        def query(self, **kwargs):
            return {"Items": []}
    class FakeResource:
        def Table(self, _name):
            return FakeTableEmpty()
    monkeypatch.setattr(kb_module, "boto3", type("X", (), {"resource": lambda self, _: FakeResource()})())
    monkeypatch.setenv("KNOWLEDGE_ITEMS_TABLE", "atrium-knowledgeitems")
    result = kb_module.kb_lookup("anything", "no-such-tenant")
    assert result["status"] == "kb_empty"


def test_citation_source_is_string(monkeypatch):
    _local_kb(monkeypatch)
    result = kb_module.kb_lookup("Do you need photos?", "glanz-ag")
    for cit in result["citations"]:
        assert isinstance(cit["source"], str)
        assert "·" in cit["source"]  # CATEGORY · Title format
