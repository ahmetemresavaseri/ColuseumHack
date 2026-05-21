"""Knowledge-base retrieval boundary for the Input Agent."""
from __future__ import annotations

import os
from typing import Any

import boto3


def kb_lookup(question: str, company_id: str, top_k: int = 4) -> dict[str, Any]:
    """Retrieve tenant-filtered context from Bedrock Knowledge Bases.

    The exact metadata filter can evolve with the KB schema; callers should rely
    only on `answer_context` and `citations`.
    """
    kb_id = os.environ.get("BEDROCK_KB_ID")
    if not kb_id:
        return {"answer_context": "", "citations": [], "status": "kb_not_configured"}

    client = boto3.client("bedrock-agent-runtime")
    response = client.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": question},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": top_k,
                "filter": {"equals": {"key": "companyId", "value": company_id}},
            }
        },
    )
    citations = []
    chunks = []
    for result in response.get("retrievalResults", []):
        text = result.get("content", {}).get("text", "")
        if text:
            chunks.append(text)
        citations.append(
            {
                "source": result.get("location", {}),
                "score": result.get("score"),
                "excerpt": text[:300],
            }
        )
    return {"answer_context": "\n\n".join(chunks), "citations": citations}
