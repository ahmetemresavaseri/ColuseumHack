"""Stage 2 — deploy the Atrium Input Agent Lambda.

Idempotent:
  - Creates IAM role atrium-input-agent-role if missing, ensures policies.
  - Packages lambdas/input_agent/ as ZIP and creates/updates the function.

Wiring NOTE (2026-05-22): the original version of this script also wired the
function into an Amazon Connect contact flow. **Amazon Connect is NOT on the
hackathon allow-list**, so all Connect-wiring steps have been removed. The
function is now invoked from API Gateway WebSocket (`$connect` / `$message` /
`$disconnect` routes) created by `infrastructure/cdk_app.py`. Add the
WS-invoke permission there, not here.

State file used to live at `scripts/.connect_state.json`; it's now optional —
if present, only its `LambdaFunctionArn` field is updated. New deployments
write a fresh `scripts/.deploy_state.json` instead.
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from botocore.exceptions import ClientError

from aws_helpers import get_account_id, iam, lambda_client, region

ROOT = Path(__file__).resolve().parent.parent
LAMBDA_DIR = ROOT / "lambdas" / "input_agent"
STATE_FILE = Path(__file__).parent / ".deploy_state.json"
LEGACY_STATE_FILE = Path(__file__).parent / ".connect_state.json"

FUNCTION_NAME = "atrium-input-agent"
ROLE_NAME = "atrium-input-agent-role"
RUNTIME = "python3.13"
HANDLER = "handler.lambda_handler"
MEMORY_MB = 1024
# Long-running for the audio WS bridge — Connect's 8s ceiling no longer applies.
# API GW WS allows up to 29s per route invocation; the Input Agent uses a
# persistent inner loop tied to the WS lifecycle, so a generous Lambda timeout
# is appropriate. (Tune downward once we know the real per-message ceiling.)
TIMEOUT_S = 900

ASSUME_ROLE_DOC = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}

# IAM policy — ONLY services on the hackathon allow-list.
# Dropped vs. previous version: kinesisvideo:*, connect:*
# Added: transcribe:*, polly:*, dynamodb:*, execute-api:* (API GW WS post),
#        bedrock-agent-runtime:Retrieve (for KB.Retrieve)
INLINE_POLICY_DOC = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "Bedrock",
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:Converse",
                "bedrock:ConverseStream",
            ],
            "Resource": "*",
        },
        {
            "Sid": "BedrockKnowledgeBaseRetrieve",
            "Effect": "Allow",
            "Action": [
                "bedrock:Retrieve",
                "bedrock:RetrieveAndGenerate",
            ],
            "Resource": "*",
        },
        {
            "Sid": "TranscribeStreaming",
            "Effect": "Allow",
            "Action": [
                "transcribe:StartStreamTranscription",
                "transcribe:StartStreamTranscriptionWebSocket",
            ],
            "Resource": "*",
        },
        {
            "Sid": "PollyNeural",
            "Effect": "Allow",
            "Action": [
                "polly:SynthesizeSpeech",
                "polly:DescribeVoices",
            ],
            "Resource": "*",
        },
        {
            "Sid": "DynamoDB",
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:DeleteItem",
                "dynamodb:Query",
                "dynamodb:BatchGetItem",
                "dynamodb:BatchWriteItem",
            ],
            "Resource": "*",
        },
        {
            "Sid": "ApiGatewayWebSocketPush",
            "Effect": "Allow",
            "Action": ["execute-api:ManageConnections"],
            "Resource": "arn:aws:execute-api:*:*:*/@connections/*",
        },
        {
            "Sid": "InvokeBrainLambda",
            "Effect": "Allow",
            "Action": ["lambda:InvokeFunction"],
            "Resource": "*",
        },
        {
            "Sid": "EventBridgeLog",
            "Effect": "Allow",
            "Action": ["events:PutEvents"],
            "Resource": "*",
        },
    ],
}

OK = "[OK]"
WAIT = "[..]"
FAIL = "[FAIL]"


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    if LEGACY_STATE_FILE.exists():
        print(f"{WAIT} Found legacy {LEGACY_STATE_FILE.name} — Connect wiring will be ignored.")
        return json.loads(LEGACY_STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def ensure_role() -> str:
    i = iam()
    try:
        r = i.get_role(RoleName=ROLE_NAME)
        print(f"{OK} IAM role exists: {r['Role']['Arn']}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        print(f"{WAIT} Creating IAM role {ROLE_NAME} ...")
        r = i.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(ASSUME_ROLE_DOC),
            Description="Execution role for Atrium Input Agent Lambda (WebRTC architecture)",
        )
        print(f"{OK} IAM role created: {r['Role']['Arn']}")

    arn = r["Role"]["Arn"]

    i.attach_role_policy(
        RoleName=ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    i.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="atrium-runtime",
        PolicyDocument=json.dumps(INLINE_POLICY_DOC),
    )
    print(f"{OK} IAM policies attached (Bedrock + Transcribe + Polly + DynamoDB + API GW WS)")
    return arn


def package_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for src in LAMBDA_DIR.rglob("*.py"):
            arcname = src.relative_to(LAMBDA_DIR).as_posix()
            z.write(src, arcname)
    buf.seek(0)
    data = buf.read()
    print(f"{OK} Packaged {len(data)} bytes from {LAMBDA_DIR}")
    return data


def function_exists(name: str) -> bool:
    try:
        lambda_client().get_function(FunctionName=name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def wait_active(name: str, timeout: int = 60) -> None:
    lc = lambda_client()
    for _ in range(timeout):
        cfg = lc.get_function_configuration(FunctionName=name)
        if cfg["State"] == "Active" and cfg.get("LastUpdateStatus", "Successful") == "Successful":
            return
        time.sleep(1)


def deploy_function(role_arn: str, zip_bytes: bytes) -> str:
    lc = lambda_client()
    if function_exists(FUNCTION_NAME):
        print(f"{WAIT} Updating existing function {FUNCTION_NAME} ...")
        lc.update_function_code(FunctionName=FUNCTION_NAME, ZipFile=zip_bytes)
        wait_active(FUNCTION_NAME)
        lc.update_function_configuration(
            FunctionName=FUNCTION_NAME,
            Runtime=RUNTIME,
            Handler=HANDLER,
            Role=role_arn,
            Timeout=TIMEOUT_S,
            MemorySize=MEMORY_MB,
        )
        wait_active(FUNCTION_NAME)
    else:
        print(f"{WAIT} Creating function {FUNCTION_NAME} ...")
        for attempt in range(12):
            try:
                lc.create_function(
                    FunctionName=FUNCTION_NAME,
                    Runtime=RUNTIME,
                    Role=role_arn,
                    Handler=HANDLER,
                    Code={"ZipFile": zip_bytes},
                    Timeout=TIMEOUT_S,
                    MemorySize=MEMORY_MB,
                    Description="Atrium Input Agent — WebRTC audio WS bridge (Transcribe + Claude + Polly)",
                )
                break
            except ClientError as e:
                if e.response["Error"].get("Code") == "InvalidParameterValueException" and "cannot be assumed" in str(e):
                    print(f"     IAM role not assumable yet, retry {attempt+1}/12 in 5s")
                    time.sleep(5)
                    continue
                raise
        wait_active(FUNCTION_NAME)
    info = lc.get_function(FunctionName=FUNCTION_NAME)
    arn = info["Configuration"]["FunctionArn"]
    print(f"{OK} Function deployed: {arn}")
    return arn


def main() -> int:
    state = load_state()
    print(f"=== Deploying {FUNCTION_NAME} (account={get_account_id()}, region={region()}) ===")
    print("    Architecture: API Gateway WebSocket (audio) — Connect wiring removed.")
    role_arn = ensure_role()
    zip_bytes = package_zip()
    function_arn = deploy_function(role_arn, zip_bytes)

    state.update({
        "LambdaFunctionArn": function_arn,
        "LambdaFunctionName": FUNCTION_NAME,
        "LambdaRoleArn": role_arn,
        "Architecture": "webrtc-api-gw-ws",
    })
    save_state(state)

    print("\n=== Done. ===")
    print(f"     Function ARN: {function_arn}")
    print(f"     Logs: aws logs tail /aws/lambda/{FUNCTION_NAME} --follow (or use scripts/tail_logs.py)")
    print("     Next: wire this ARN as the integration target on the audio API GW WS routes")
    print("           ($connect, $message, $disconnect) in infrastructure/cdk_app.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
