"""Knowledge-base retrieval boundary for the Input Agent.

DynamoDB-backed lookup for demo reliability. Reads `KnowledgeItems` records
scoped to a single `companyId`, then ranks them with a simple keyword/topic
score so the highest-signal entries surface first. Returns the same shape
(answer_context + citations) the agent already consumes.
"""
from __future__ import annotations

import os
import re
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

_TOKEN = re.compile(r"[a-zA-ZäöüÄÖÜß]+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text or "") if len(t) > 2}


def _score(item: dict[str, Any], query_tokens: set[str]) -> int:
    score = 0
    for kw in item.get("keywords", []) or []:
        if kw.lower() in query_tokens:
            score += 3
    score += len(query_tokens & _tokens(item.get("topic", "")))
    score += len(query_tokens & _tokens(item.get("title", "")))
    score += len(query_tokens & _tokens(item.get("body", ""))) // 2
    return score


def kb_lookup(question: str, company_id: str, top_k: int = 4) -> dict[str, Any]:
    table_name = os.environ.get("KNOWLEDGE_ITEMS_TABLE")
    if not table_name:
        return {"answer_context": "", "citations": [], "status": "kb_not_configured"}

    table = boto3.resource("dynamodb").Table(table_name)
    response = table.query(KeyConditionExpression=Key("companyId").eq(company_id))
    items = response.get("Items", [])
    if not items:
        return {"answer_context": "", "citations": [], "status": "kb_empty"}

    query_tokens = _tokens(question)
    ranked = sorted(items, key=lambda it: _score(it, query_tokens), reverse=True)
    selected = ranked[:top_k] if query_tokens else items[:top_k]

    chunks: list[str] = []
    citations: list[dict[str, Any]] = []
    for item in selected:
        title = item.get("title", "")
        body = item.get("body", "")
        chunks.append(f"{title}\n{body}".strip())
        citations.append(
            {
                "source": {
                    "companyId": item["companyId"],
                    "itemId": item["itemId"],
                    "category": item.get("category"),
                },
                "score": _score(item, query_tokens) if query_tokens else None,
                "excerpt": body[:300],
            }
        )
    return {"answer_context": "\n\n".join(chunks), "citations": citations}
