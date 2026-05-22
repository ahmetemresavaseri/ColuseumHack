"""Knowledge-base retrieval boundary for the Input Agent.

DynamoDB-backed lookup for demo reliability. Reads `KnowledgeItems` records
scoped to a single `companyId`, then ranks them with a simple keyword/topic
score so the highest-signal entries surface first. Returns the canonical
shape consumed by the agent + wall:

    {
      "answer_context": "<concatenated bodies>",
      "citations": [{"source": "<readable label>", "excerpt": "<body>", ...}],
      "top_score": <int>,
      "status": "ok" | "kb_empty" | "kb_no_match" | "kb_not_configured",
    }

`top_score` lets the caller threshold a "refuse" path — Phase 3's contract
says we must say "I don't have that information" when retrieval is weak,
never invent.
"""
from __future__ import annotations

import os
import re
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

_TOKEN = re.compile(r"[a-zA-ZäöüÄÖÜß]+")

# Common fillers that inflate scores when they happen to land in a title
# like "Can you provide a price during the call?". We strip them from
# *queries only* — keywords listed by the curator stay authoritative.
_STOPWORDS = {
    "the", "and", "are", "you", "for", "but", "with", "this", "that",
    "have", "has", "had", "can", "could", "would", "should", "will",
    "from", "into", "your", "our", "there", "their", "they", "them",
    "what", "when", "where", "who", "why", "how",
    "did", "does", "was", "were", "any", "all", "some", "one",
    "yes", "okay", "thanks", "please", "give", "tell",
}

# Minimum top-item score for the agent to *answer* from KB. Anything below
# this threshold falls back to "I don't have that information." With the
# stopword filter above, common-word noise gets stripped before scoring, so
# 2 is enough to separate signal from filler.
ANSWER_MIN_SCORE = 2

# Vocabulary that strongly hints the caller is asking about pricing — we
# boost pricing-category items when the query contains any of these.
_PRICING_VOCAB = {
    "much", "many", "price", "cost", "rate", "fee", "per", "francs",
    "chf", "eur", "usd", "dollar", "euro", "expensive", "cheap",
    "rates", "pricing", "quote", "estimate",
}


def _tokens(text: str) -> set[str]:
    raw = {t.lower() for t in _TOKEN.findall(text or "") if len(t) > 2}
    return raw - _STOPWORDS


def _is_pricing_query(query_raw_tokens: set[str]) -> bool:
    return bool(query_raw_tokens & _PRICING_VOCAB)


def _score(
    item: dict[str, Any],
    query_tokens: set[str],
    is_pricing_query: bool = False,
) -> int:
    score = 0
    for kw in item.get("keywords", []) or []:
        if kw.lower() in query_tokens:
            score += 3
    score += len(query_tokens & _tokens(item.get("topic", "")))
    score += len(query_tokens & _tokens(item.get("title", "")))
    score += len(query_tokens & _tokens(item.get("body", ""))) // 2
    # Category boost: pricing-flavored questions strongly prefer pricing items.
    if is_pricing_query and item.get("category") == "pricing":
        score += 5
    return score


def _citation_source(item: dict[str, Any]) -> str:
    """Readable label for the wall — short enough to fit a chip."""
    category = (item.get("category") or "kb").upper()
    title = item.get("title") or item.get("itemId") or "Unknown"
    return f"{category} · {title}"


def kb_lookup(question: str, company_id: str, top_k: int = 4) -> dict[str, Any]:
    table_name = os.environ.get("KNOWLEDGE_ITEMS_TABLE")
    if not table_name:
        return {
            "answer_context": "", "citations": [],
            "top_score": 0, "status": "kb_not_configured",
        }

    table = boto3.resource("dynamodb").Table(table_name)
    response = table.query(KeyConditionExpression=Key("companyId").eq(company_id))
    items = response.get("Items", [])
    if not items:
        return {
            "answer_context": "", "citations": [],
            "top_score": 0, "status": "kb_empty",
        }

    query_tokens = _tokens(question)
    # Raw tokens (without stopword filter) are used to detect pricing intent
    # since "how much" relies on these short words being preserved here.
    raw_tokens = {t.lower() for t in _TOKEN.findall(question or "") if len(t) > 2}
    pricing_q = _is_pricing_query(raw_tokens)
    scored = (
        [(_score(item, query_tokens, pricing_q), item) for item in items]
        if query_tokens
        else [(0, item) for item in items]
    )
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top_score = scored[0][0] if scored else 0

    if query_tokens and top_score == 0:
        return {
            "answer_context": "", "citations": [],
            "top_score": 0, "status": "kb_no_match",
        }

    selected = scored[:top_k]
    chunks: list[str] = []
    citations: list[dict[str, Any]] = []
    for score, item in selected:
        body = item.get("body", "")
        chunks.append(f"{item.get('title','')}\n{body}".strip())
        citations.append({
            "source": _citation_source(item),
            "excerpt": body[:280],
            "itemId": item.get("itemId"),
            "score": score,
        })
    return {
        "answer_context": "\n\n".join(chunks),
        "citations": citations,
        "top_score": top_score,
        "status": "ok",
    }
