r"""Smoke test — verifies AWS creds + every Bedrock/AI service Atrium uses.

Run after sourcing .env.local.ps1:
    . .\.env.local.ps1
    python smoke_test.py

Exit code is non-zero if any check fails — useful for CI / pre-build sanity.

Service whitelist for the hackathon (only these may be used):
  - Bedrock (Claude Sonnet 4.6 only)            — Brain + voice-LLM
  - Amazon Transcribe Streaming                  — caller speech -> text
  - Amazon Polly                                 — agent text -> speech
  - Amazon Lex V2, API Gateway, Lambda, DynamoDB, S3, S3 Vectors,
    CloudFront/Amplify, EventBridge              — infra (not probed here)

Explicitly NOT allowed (and therefore NOT probed):
  - Bedrock Nova Sonic (not on the Bedrock model allow-list)
  - Amazon Connect / Q in Connect                — replaced by Browser WebRTC
  - AppSync                                      — replaced by API GW WebSocket
"""
from __future__ import annotations

import io
import sys
import traceback

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, "scripts")
from aws_helpers import (  # noqa: E402
    bedrock,
    bedrock_runtime,
    polly,
    region,
    sts,
    transcribe_streaming,
)

CLAUDE_MODEL_ID = "anthropic.claude-sonnet-4-6"
CLAUDE_INFERENCE_PROFILE = "us.anthropic.claude-sonnet-4-6"

OK = "[OK]"
FAIL = "[FAIL]"


def check_sts() -> bool:
    try:
        ident = sts().get_caller_identity()
        print(f"{OK} STS: {ident['Arn']} (account {ident['Account']}, region {region()})")
        return True
    except Exception as e:
        print(f"{FAIL} STS failed: {e}")
        return False


def check_bedrock_models() -> bool:
    try:
        models = bedrock().list_foundation_models()["modelSummaries"]
        ids = {m["modelId"] for m in models}
        if CLAUDE_MODEL_ID in ids:
            print(f"{OK} Bedrock model available: {CLAUDE_MODEL_ID}")
            return True
        print(f"{FAIL} Bedrock model MISSING: {CLAUDE_MODEL_ID}")
        return False
    except Exception as e:
        print(f"{FAIL} Bedrock list_foundation_models: {e}")
        return False


def check_claude_converse() -> bool:
    try:
        rt = bedrock_runtime()
        resp = rt.converse(
            modelId=CLAUDE_INFERENCE_PROFILE,
            messages=[{"role": "user", "content": [{"text": "Respond with exactly: PONG"}]}],
            inferenceConfig={"maxTokens": 8, "temperature": 0.0},
        )
        out = resp["output"]["message"]["content"][0]["text"].strip()
        print(f"{OK} Claude Sonnet 4.6 reachable. Response: {out!r}")
        return True
    except ClientError as e:
        print(f"{FAIL} Claude converse failed: {e.response['Error'].get('Code')}: {e}")
        return False
    except Exception as e:
        print(f"{FAIL} Claude converse failed: {e}")
        return False


def check_polly() -> bool:
    try:
        p = polly()
        resp = p.synthesize_speech(
            Text="Hello from Atrium.",
            OutputFormat="mp3",
            VoiceId="Joanna",
            Engine="neural",
        )
        stream = resp["AudioStream"]
        first = stream.read(64)
        if not first:
            print(f"{FAIL} Polly returned empty audio stream")
            return False
        print(f"{OK} Polly neural TTS reachable (got {len(first)}+ bytes of MP3)")
        return True
    except ClientError as e:
        print(f"{FAIL} Polly synthesize_speech: {e.response['Error'].get('Code')}: {e}")
        return False
    except Exception as e:
        print(f"{FAIL} Polly synthesize_speech: {e}")
        return False


def check_transcribe_streaming() -> bool:
    """Verifies the role can reach Amazon Transcribe and that some streaming
    surface is available. We don't open a real stream (would need audio).

    Note: real-time streaming uses HTTP/2 — production code should use the
    `amazon-transcribe` (aiobotocore-based) SDK, not boto3. Here we just want
    a fast reachability probe.
    """
    # Reachability via the classic (non-streaming) transcribe client.
    try:
        t = boto3.client("transcribe", region_name=region())
        t.list_vocabularies(MaxResults=1)
        print(f"{OK} Transcribe service reachable (list_vocabularies OK)")
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code == "AccessDeniedException":
            print(f"{FAIL} Transcribe AccessDenied — workshop role lacks transcribe:* perms")
            return False
        print(f"{FAIL} Transcribe probe failed: {code}: {e}")
        return False
    except Exception as e:
        traceback.print_exc()
        print(f"{FAIL} Transcribe probe failed: {e}")
        return False

    # Best-effort: probe the transcribe-streaming service model if available.
    try:
        c = transcribe_streaming()
        ops = c.meta.service_model.operation_names
        if "StartStreamTranscription" in ops:
            print(f"{OK} Transcribe Streaming API surface exposes StartStreamTranscription")
        else:
            print(f"     (transcribe-streaming client available but no StartStreamTranscription op — "
                  f"production code should use the amazon-transcribe SDK)")
    except Exception as e:
        # transcribe-streaming sync client may not be available in this boto3 build;
        # that's fine — the real-time loop will use the amazon-transcribe SDK.
        print(f"     (transcribe-streaming sync client not available in this boto3: {e!r} "
              f"— production code uses the amazon-transcribe SDK instead, this is non-fatal)")
    return True


def main() -> int:
    print(f"=== Atrium smoke test — boto3 {boto3.__version__} ===")
    checks = [
        ("STS", check_sts),
        ("Bedrock models", check_bedrock_models),
        ("Claude Sonnet 4.6 converse", check_claude_converse),
        ("Polly (neural TTS)", check_polly),
        ("Transcribe Streaming (ASR)", check_transcribe_streaming),
    ]
    results = {name: fn() for name, fn in checks}
    print("\n=== Summary ===")
    for name, ok in results.items():
        marker = OK if ok else FAIL
        print(f"  {marker} {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
