"""Amazon Transcribe Streaming boundary.

The Phase 1 Input Agent forwards browser PCM frames to Transcribe Streaming and
emits `TranscriptTurn` events for every finalized utterance. The real
bidirectional stream is established with `amazon-transcribe-streaming-sdk` (or
`boto3`'s low-level client) — this module keeps the contract small so the
handler can be unit-tested without AWS in the loop.
"""
from __future__ import annotations

import os
from typing import Iterable, Iterator, Protocol


class TranscriptionEvent(Protocol):
    text: str
    is_final: bool


class TranscribeClient:
    """Boundary that the handler uses to push PCM frames and pull text."""

    def __init__(self, language_code: str | None = None) -> None:
        self.language_code = language_code or os.environ.get(
            "TRANSCRIBE_LANGUAGE_CODE", "en-US"
        )

    def open(self) -> None:
        """Open the bidirectional stream.

        Real implementation will spin up the Transcribe Streaming session here.
        Phase 1 leaves this as a no-op so a text-turn fallback path can drive
        the rest of the pipeline.
        """

    def push_audio(self, frame: bytes) -> None:
        """Forward a single PCM16 frame to Transcribe."""

    def events(self) -> Iterator[TranscriptionEvent]:
        """Yield transcript events as they arrive.

        Real implementation streams partials + finals; Phase 1 leaves it empty
        so the handler reads from the deterministic text-turn path instead.
        """
        return iter(())

    def close(self) -> None:
        """Close the upstream stream cleanly."""


def chunks_to_pcm16(chunks: Iterable[bytes]) -> bytes:
    """Convenience helper for tests/local simulations."""
    return b"".join(chunks)
