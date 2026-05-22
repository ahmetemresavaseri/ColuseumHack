"""Bedrock Claude Converse boundary.

Phase 1 uses Claude Sonnet 4.6 via the Bedrock Converse API for the turn loop
(text in -> text out + tool-use). The handler hits this boundary; the
boundary is responsible for prompt assembly, tool-call decoding, and the
deterministic fallback used when AWS credentials/model access are not
available locally.
"""
from __future__ import annotations

import json
import os
from typing import Any

try:
    import boto3  # type: ignore
except ImportError:  # pragma: no cover - boto3 missing in some local envs
    boto3 = None  # type: ignore


DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"


class BedrockClaudeClient:
    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or os.environ.get("BRAIN_MODEL_ID", DEFAULT_MODEL)
        self._client = None

    def _runtime(self):
        if self._client is None and boto3 is not None:
            self._client = boto3.client("bedrock-runtime")
        return self._client

    def is_available(self) -> bool:
        return boto3 is not None and bool(os.environ.get("AWS_REGION"))

    def converse(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run a single Converse turn and return the raw Bedrock response."""
        runtime = self._runtime()
        if runtime is None:
            raise RuntimeError("bedrock-runtime client unavailable")
        payload: dict[str, Any] = {
            "modelId": self.model_id,
            "system": [{"text": system_prompt}],
            "messages": messages,
        }
        if tools:
            payload["toolConfig"] = {"tools": tools}
        return runtime.converse(**payload)

    @staticmethod
    def decode_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
        """Pull `(name, args)` out of a Converse tool-use response."""
        calls: list[dict[str, Any]] = []
        for block in (
            response.get("output", {}).get("message", {}).get("content", []) or []
        ):
            tool_use = block.get("toolUse")
            if not tool_use:
                continue
            name = tool_use.get("name")
            args = tool_use.get("input") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append({"name": name, "args": args})
        return calls

    @staticmethod
    def decode_text(response: dict[str, Any]) -> str:
        for block in (
            response.get("output", {}).get("message", {}).get("content", []) or []
        ):
            if "text" in block:
                return block["text"]
        return ""
