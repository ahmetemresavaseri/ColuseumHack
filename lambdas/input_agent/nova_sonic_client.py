"""Nova Sonic client boundary for Stage 3 bidirectional audio."""
from __future__ import annotations

import os
from typing import Iterable, Iterator

import boto3


class NovaSonicClient:
    """Thin wrapper so the Input Agent handler is not coupled to boto3 details."""

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or os.environ.get("NOVA_SONIC_MODEL_ID", "amazon.nova-sonic-v1:0")
        self.client = boto3.client("bedrock-runtime")

    def stream_audio(self, audio_events: Iterable[bytes], system_prompt: str) -> Iterator[bytes]:
        """Yield model audio chunks.

        This is intentionally a contract stub until the Connect/KVS audio bridge
        is wired. Keeping the method shape now lets the voice owner and handler
        owner work independently.
        """
        raise NotImplementedError("Nova Sonic bidirectional stream is wired in Stage 3.")
