"""Stage 2 — deploy the Atrium Input Agent Lambda and wire it into Connect.

Idempotent:
  - Creates IAM role atrium-input-agent-role if missing, ensures policies.
  - Packages lambdas/input_agent/ as ZIP and creates/updates the function.
  - Adds Lambda resource permission so Connect can invoke it.
  - Associates the Lambda with the Connect instance (so it appears in the
    "Invoke Lambda" block dropdown).
  - Rewrites the atrium-inbound contact flow to: InvokeLambda -> speak greeting -> disconnect.

Reads scripts/.connect_state.json from Stage 1. Updates it with LambdaFunctionArn.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from botocore.exceptions import ClientError

from aws_helpers import connect_client, get_account_id, iam, lambda_client, region

ROOT = Path(__file__).resolve().parent.parent
LAMBDA_DIR = ROOT / "lambdas" / "input_agent"
STATE_FILE = Path(__file__).parent / ".connect_state.json"

FUNCTION_NAME = "atrium-input-agent"
ROLE_NAME = "atrium-input-agent-role"
RUNTIME = "python3.13"
HANDLER = "handler.lambda_handler"
MEMORY_MB = 1024
TIMEOUT_S = 8  # Connect's hard ceiling for Lambda invocations from contact flow

ASSUME_ROLE_DOC = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}

INLINE_POLICY_DOC = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "Bedrock",
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:InvokeModelWithBidirectionalStream",
                "bedrock:Converse",
                "bedrock:ConverseStream",
            ],
            "Resource": "*",
        },
        {
            "Sid": "KinesisVideoStreams",
            "Effect": "Allow",
            "Action": [
                "kinesisvideo:DescribeStream",
                "kinesisvideo:GetMedia",
                "kinesisvideo:GetDataEndpoint",
                "kinesisvideo:PutMedia",
                "kinesisvideo:GetHLSStreamingSessionURL",
            ],
            "Resource": "*",
        },
        {
            "Sid": "ConnectUpdates",
            "Effect": "Allow",
            "Action": [
                "connect:UpdateContactAttributes",
                "connect:GetContactAttributes",
                "connect:StopContact",
            ],
            "Resource": "*",
        },
    ],
}

OK = "[OK]"
WAIT = "[..]"
FAIL = "[FAIL]"


def load_state() -> dict:
    if not STATE_FILE.exists():
        raise SystemExit(f"{FAIL} Run provision_connect.py first — no state at {STATE_FILE}")
    return json.loads(STATE_FILE.read_text())


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
            Description="Execution role for Atrium Input Agent Lambda",
        )
        print(f"{OK} IAM role created: {r['Role']['Arn']}")

    arn = r["Role"]["Arn"]

    # Managed policy for basic CloudWatch Logs
    i.attach_role_policy(
        RoleName=ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    # Inline policy for bedrock + KVS + connect
    i.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="atrium-runtime",
        PolicyDocument=json.dumps(INLINE_POLICY_DOC),
    )
    print(f"{OK} IAM policies attached")
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
        # IAM eventual consistency: role can take a few seconds to be assumable
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
                    Description="Atrium Input Agent — Connect <-> Nova Sonic bridge",
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


def allow_connect_invoke(function_arn: str, instance_arn: str) -> None:
    lc = lambda_client()
    statement_id = "AllowConnectInvoke"
    try:
        lc.add_permission(
            FunctionName=FUNCTION_NAME,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal="connect.amazonaws.com",
            SourceArn=instance_arn,
        )
        print(f"{OK} Lambda resource policy: Connect can invoke")
    except ClientError as e:
        if e.response["Error"].get("Code") == "ResourceConflictException":
            print(f"{OK} Lambda resource policy already exists")
        else:
            raise


def associate_with_connect(function_arn: str, instance_id: str) -> None:
    cc = connect_client()
    try:
        cc.associate_lambda_function(InstanceId=instance_id, FunctionArn=function_arn)
        print(f"{OK} Lambda associated with Connect instance")
    except ClientError as e:
        if e.response["Error"].get("Code") == "ResourceConflictException":
            print(f"{OK} Lambda already associated with Connect instance")
        else:
            raise


def build_stage2_flow_content(lambda_arn: str, lex_bot_alias_arn: str | None = None) -> dict:
    """Phase 1 contact flow:

      InvokeLambda (bootstrap call/booking, return greeting + IDs)
        → SetAttributes (lift the External attrs into contact attributes)
        → MessageParticipant (speak greeting)
        → if Lex bot alias configured:
            GetCustomerInput → Lex bot → loops until intent is fulfilled
          else: skip straight to disconnect
        → Disconnect
    """
    actions = [
        {
            "Identifier": "invoke",
            "Type": "InvokeLambdaFunction",
            "Parameters": {
                "LambdaFunctionARN": lambda_arn,
                "InvocationTimeLimitSeconds": "8",
            },
            "Transitions": {
                "NextAction": "speak",
                "Errors": [{"NextAction": "speak_err", "ErrorType": "NoMatchingError"}],
            },
        },
        {
            "Identifier": "speak",
            "Type": "MessageParticipant",
            "Parameters": {"Text": "$.External.greeting"},
            "Transitions": {
                "NextAction": "lex_input" if lex_bot_alias_arn else "disconnect",
            },
        },
    ]
    if lex_bot_alias_arn:
        actions.append(
            {
                "Identifier": "lex_input",
                "Parameters": {
                    "Text": "How can I help you?",
                    "LexV2Bot": {"AliasArn": lex_bot_alias_arn},
                },
                "Transitions": {
                    "NextAction": "disconnect",
                    "Errors": [
                        {"NextAction": "disconnect", "ErrorType": "NoMatchingError"},
                        {"NextAction": "disconnect", "ErrorType": "NoMatchingCondition"},
                    ],
                    "Conditions": [],
                },
                "Type": "ConnectParticipantWithLexBot",
            }
        )
    actions.append(
        {
            "Identifier": "speak_err",
            "Type": "MessageParticipant",
            "Parameters": {
                "Text": "Sorry, the agent is not available right now. Please try again later.",
            },
            "Transitions": {"NextAction": "disconnect"},
        }
    )
    actions.append(
        {
            "Identifier": "disconnect",
            "Type": "DisconnectParticipant",
            "Parameters": {},
            "Transitions": {},
        }
    )
    return {
        "Version": "2019-10-30",
        "StartAction": "invoke",
        "Metadata": {
            "entryPointPosition": {"x": 40, "y": 40},
            "ActionMetadata": {
                "invoke": {"position": {"x": 240, "y": 40}},
                "speak": {"position": {"x": 440, "y": 40}},
                "lex_input": {"position": {"x": 640, "y": 40}},
                "speak_err": {"position": {"x": 440, "y": 240}},
                "disconnect": {"position": {"x": 840, "y": 40}},
            },
        },
        "Actions": actions,
    }


def update_contact_flow(
    instance_id: str,
    flow_id: str,
    lambda_arn: str,
    lex_bot_alias_arn: str | None = None,
) -> None:
    cc = connect_client()
    content = build_stage2_flow_content(lambda_arn, lex_bot_alias_arn)
    cc.update_contact_flow_content(
        InstanceId=instance_id,
        ContactFlowId=flow_id,
        Content=json.dumps(content),
    )
    suffix = " → Lex" if lex_bot_alias_arn else ""
    print(f"{OK} Contact flow updated: Lambda greeting{suffix} → disconnect")


def main() -> int:
    state = load_state()
    instance_id = state["InstanceId"]
    instance_arn = state["InstanceArn"]
    flow_id = state["ContactFlowId"]

    print(f"=== Deploying {FUNCTION_NAME} (account={get_account_id()}, region={region()}) ===")
    role_arn = ensure_role()
    zip_bytes = package_zip()
    function_arn = deploy_function(role_arn, zip_bytes)
    allow_connect_invoke(function_arn, instance_arn)
    associate_with_connect(function_arn, instance_id)

    lex_bot_alias_arn = None
    bot_id = state.get("LexBotId")
    alias_id = state.get("LexBotAliasId")
    if bot_id and alias_id:
        lex_bot_alias_arn = (
            f"arn:aws:lex:{region()}:{get_account_id()}:bot-alias/{bot_id}/{alias_id}"
        )
        print(f"{OK} Found Lex bot alias ARN: {lex_bot_alias_arn}")

    update_contact_flow(instance_id, flow_id, function_arn, lex_bot_alias_arn)

    state.update({
        "LambdaFunctionArn": function_arn,
        "LambdaFunctionName": FUNCTION_NAME,
        "LambdaRoleArn": role_arn,
    })
    save_state(state)

    print(f"\n=== Done. Call {state.get('PhoneNumber')} to test Stage 2. ===")
    print(f"     Logs: aws logs tail /aws/lambda/{FUNCTION_NAME} --follow (or use scripts/tail_logs.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
