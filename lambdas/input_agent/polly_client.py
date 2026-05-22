"""Amazon Polly Neural TTS boundary."""
from __future__ import annotations

import os

try:
    import boto3  # type: ignore
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore


class PollyClient:
    def __init__(self, voice_id: str | None = None) -> None:
        self.voice_id = voice_id or os.environ.get("POLLY_VOICE_ID", "Joanna")
        self._client = None

    def _polly(self):
        if self._client is None and boto3 is not None:
            self._client = boto3.client("polly")
        return self._client

    def is_available(self) -> bool:
        return boto3 is not None

    def synthesize_mp3(self, text: str) -> bytes:
        """Return MP3 bytes for the supplied text.

        Returns an empty payload if AWS credentials/clients are not available;
        the caller is expected to fall back to text-only responses.
        """
        if not text:
            return b""
        client = self._polly()
        if client is None:
            return b""
        response = client.synthesize_speech(
            VoiceId=self.voice_id,
            Text=text,
            Engine="neural",
            OutputFormat="mp3",
        )
        stream = response.get("AudioStream")
        if stream is None:
            return b""
        return stream.read()
