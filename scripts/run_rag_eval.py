"""Run the Phase 3 RAG + hallucination evals against the deterministic kb_lookup.

Reads from `eval/rag_eval.csv` and `eval/hallucination_test.md`, runs each
question through `kb_lookup`, applies the handler's `_compose_faq_answer`
policy, and prints a pass/fail summary.

`kb_lookup` reads from the seed file when `KB_SEED_FILE` is set — no AWS or
DynamoDB needed to run this locally.

Usage:
    python scripts/run_rag_eval.py
"""
from __future__ import annotations

import csv
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambdas" / "input_agent"))


def _build_local_kb_lookup() -> callable:
    """Bind a kb_lookup that reads the JSON seed instead of DynamoDB."""
    import json, re
    from kb import (  # noqa: F401
        ANSWER_MIN_SCORE, _PRICING_VOCAB, _TOKEN, _score, _tokens, _citation_source,
    )

    items = json.loads((ROOT / "infrastructure/seed/knowledge_items.json").read_text())

    def lookup(question: str, company_id: str, top_k: int = 4):
        scoped = [it for it in items if it.get("companyId") == company_id]
        if not scoped:
            return {"answer_context": "", "citations": [],
                    "top_score": 0, "status": "kb_empty"}
        query_tokens = _tokens(question)
        raw_tokens = {t.lower() for t in _TOKEN.findall(question or "") if len(t) > 2}
        is_pricing = bool(raw_tokens & _PRICING_VOCAB)
        scored = [(_score(it, query_tokens, is_pricing), it) for it in scoped] if query_tokens \
            else [(0, it) for it in scoped]
        scored.sort(key=lambda p: p[0], reverse=True)
        top_score = scored[0][0] if scored else 0
        if query_tokens and top_score == 0:
            return {"answer_context": "", "citations": [],
                    "top_score": 0, "status": "kb_no_match"}
        citations = [{
            "source": _citation_source(it),
            "excerpt": (it.get("body") or "")[:280],
            "itemId": it.get("itemId"),
            "score": s,
        } for s, it in scored[:top_k]]
        return {
            "answer_context": "\n\n".join((it.get("title", "") + "\n" + it.get("body", "")).strip() for _, it in scored[:top_k]),
            "citations": citations,
            "top_score": top_score,
            "status": "ok",
        }

    return lookup, ANSWER_MIN_SCORE


REFUSAL = "I don't have that information."


def _compose(kb_result, min_score):
    status = kb_result.get("status")
    citations = kb_result.get("citations") or []
    top = int(kb_result.get("top_score") or 0)
    if status != "ok" or top < min_score or not citations:
        return REFUSAL, []
    head = citations[0]
    body = (head.get("excerpt") or "").rstrip(". ").strip()
    if not body:
        return REFUSAL, []
    title = (head.get("source") or "").split("·", 1)[-1].strip()
    if title and title.lower() not in body.lower()[:60]:
        return f"{title}. {body}.", citations[:2]
    return f"{body}.", citations[:2]


def run_rag_eval(lookup, min_score) -> tuple[int, int, list[str]]:
    csv_path = ROOT / "eval" / "rag_eval.csv"
    rows = list(csv.DictReader(csv_path.open()))
    passed = 0
    failures = []
    print(f"=== RAG eval ({len(rows)} cases) ===")
    for row in rows:
        result = lookup(row["question"], row["company_id"], top_k=3)
        answer, citations = _compose(result, min_score)
        cit_ids = [c.get("itemId") for c in citations]
        ok = (
            row["expected_item_id"] in cit_ids
            and row["expected_answer_contains"].lower() in answer.lower()
        )
        flag = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failures.append(
                f"  {row['case_id']}: expected {row['expected_item_id']} containing {row['expected_answer_contains']!r}\n"
                f"    got {cit_ids} answer={answer[:120]!r}"
            )
        print(f"  [{flag}] {row['case_id']:10s} {row['question'][:60]}")
    return passed, len(rows), failures


def run_hallucination_eval(lookup, min_score) -> tuple[int, int, list[str]]:
    md_path = ROOT / "eval" / "hallucination_test.md"
    cases = []
    for line in md_path.read_text().splitlines():
        m = re.match(r"\|\s*(H-\d+)\s*\|\s*(.+?)\s*\|", line)
        if m:
            cases.append((m.group(1), m.group(2)))
    passed = 0
    failures = []
    print(f"\n=== Hallucination eval ({len(cases)} cases) ===")
    for case_id, prompt in cases:
        result = lookup(prompt, "glanz-ag", top_k=3)
        answer, citations = _compose(result, min_score)
        ok = answer == REFUSAL and not citations
        flag = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failures.append(
                f"  {case_id}: expected refusal, got citations={[c.get('itemId') for c in citations]} answer={answer[:120]!r}"
            )
        print(f"  [{flag}] {case_id:6s} {prompt[:60]}")
    return passed, len(cases), failures


def main() -> int:
    lookup, min_score = _build_local_kb_lookup()
    rag_pass, rag_total, rag_failures = run_rag_eval(lookup, min_score)
    h_pass, h_total, h_failures = run_hallucination_eval(lookup, min_score)
    print(f"\n=== Summary ===")
    print(f"RAG:           {rag_pass}/{rag_total} passing  (target: >= 8)")
    print(f"Hallucination: {h_pass}/{h_total} passing  (target: 5/5)")
    for f in rag_failures + h_failures:
        print(f)
    rag_ok = rag_pass >= 8
    h_ok = h_pass == h_total
    return 0 if (rag_ok and h_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
