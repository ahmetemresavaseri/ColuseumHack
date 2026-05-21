r"""Smoke test — verifies AWS creds, Bedrock Claude Sonnet 4.6, and Nova Sonic v2.

Run after sourcing .env.local.ps1 in your shell:
    . .\.env.local.ps1
    python smoke_test.py

Exit code is non-zero if any check fails — useful for CI / pre-build sanity.
"""
from __future__ import annotations

import sys
import traceback

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, "scripts")
from aws_helpers import bedrock, bedrock_runtime, region, sts  # noqa: E402

CLAUDE_MODEL_ID = "anthropic.claude-sonnet-4-6"  # for list_foundation_models check
CLAUDE_INFERENCE_PROFILE = "us.anthropic.claude-sonnet-4-6"  # for converse() — cross-region routing required
NOVA_SONIC_MODEL_ID = "amazon.nova-2-sonic-v1:0"

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
        ok = True
        for needed in (CLAUDE_MODEL_ID, NOVA_SONIC_MODEL_ID):
            if needed in ids:
                print(f"{OK} Bedrock model available: {needed}")
            else:
                print(f"{FAIL} Bedrock model MISSING: {needed}")
                ok = False
        return ok
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


def check_nova_sonic_api() -> bool:
    """We don't open a full bidi session (audio required) — but we verify:
    1. The operation exists in this boto3 build.
    2. We can invoke a non-bidi `invoke_model` against Nova Sonic and at least
       reach the validation layer (which tells us auth + model access work).
    """
    try:
        rt = bedrock_runtime()
        ops = rt.meta.service_model.operation_names
        if "InvokeModelWithBidirectionalStream" not in ops:
            print(f"{FAIL} boto3 does not expose InvokeModelWithBidirectionalStream "
                  f"(need newer boto3). Available: {sorted(ops)}")
            return False
        print(f"{OK} boto3 exposes InvokeModelWithBidirectionalStream")
        # Probe permission via a deliberately invalid invoke_model body — we expect
        # a ValidationException (means auth+modelAccess OK) rather than AccessDenied.
        try:
            rt.invoke_model(modelId=NOVA_SONIC_MODEL_ID, body=b"{}")
            # If it actually succeeds (unlikely), that's also fine.
            print(f"{OK} Nova Sonic invoke_model probe accepted")
            return True
        except ClientError as e:
            code = e.response["Error"].get("Code", "")
            if code in ("ValidationException", "ValidationError"):
                print(f"{OK} Nova Sonic auth + model access OK (got expected ValidationException)")
                return True
            if code == "AccessDeniedException":
                print(f"{FAIL} Nova Sonic AccessDenied — model access not granted to this role")
                return False
            print(f"{FAIL} Nova Sonic unexpected error: {code}: {e}")
            return False
    except Exception as e:
        traceback.print_exc()
        print(f"{FAIL} Nova Sonic probe failed: {e}")
        return False


def main() -> int:
    print(f"=== Atrium smoke test — boto3 {boto3.__version__} ===")
    checks = [
        ("STS", check_sts),
        ("Bedrock models", check_bedrock_models),
        ("Claude Sonnet 4.6 converse", check_claude_converse),
        ("Nova Sonic v2 API + access", check_nova_sonic_api),
    ]
    results = {name: fn() for name, fn in checks}
    print("\n=== Summary ===")
    for name, ok in results.items():
        marker = OK if ok else FAIL
        print(f"  {marker} {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
