"""Deploy the Atrium Input Agent Lambda.

Idempotent:
  - Creates IAM role atrium-input-agent-role if missing, ensures policies.
  - Packages lambdas/input_agent/ as ZIP and creates/updates the function.
  - Grants Amazon Lex permission to invoke the function (idempotent).

Wiring: the function is the CodeHook target of an Amazon Lex V2 bot, which is
in turn invoked from the Connect contact flow's GetCustomerInput block. Run
`scripts/provision_lex.py` after deploy to provision the bot and wire the
arn, then `scripts/provision_connect.py` to install the contact flow.

State is written to `scripts/.deploy_state.json`.
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
# Lex CodeHook invocations are short turns (one round-trip per caller utterance).
# Bedrock Converse with tool-use can take ~3-8s; 30s is a safe ceiling that
# stays well under Lex's 30s synchronous CodeHook limit.
TIMEOUT_S = 30

ASSUME_ROLE_DOC = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}

# IAM policy for the Lex-CodeHook path:
#   - Bedrock Converse + KB Retrieve   (the LLM turn loop)
#   - DynamoDB                          (slot persistence, company config, calls)
#   - Lambda invoke                     (Brain Lambda for compute_price)
# Polly + Transcribe perms are kept for the future browser-mic streaming path
# but unused on the Lex path (Lex handles ASR + TTS).
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


def _pip_install_requirements(dest: Path) -> None:
    """Install requirements.txt into `dest` so they ship inside the Lambda zip."""
    req = LAMBDA_DIR / "requirements.txt"
    if not req.exists():
        return
    # Skip if the file is effectively empty (only comments / blank lines).
    has_pkg = any(
        line.strip() and not line.strip().startswith("#")
        for line in req.read_text(encoding="utf-8").splitlines()
    )
    if not has_pkg:
        return

    print(f"{WAIT} pip install -r requirements.txt -> {dest.name}/ ...")
    cmd = [
        sys.executable, "-m", "pip", "install",
        "-r", str(req),
        "-t", str(dest),
        "--quiet", "--no-compile",
        # Lambda Python 3.13 runs Linux x86_64; force matching wheels.
        "--platform", "manylinux2014_x86_64",
        "--only-binary=:all:",
        "--python-version", "3.13",
        "--implementation", "cp",
        "--upgrade",
    ]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as exc:
        # Fall back to a permissive install if cross-platform wheel pick fails
        # (e.g. dev box on macOS arm64 with no manylinux wheel cached).
        print(f"{WAIT} cross-platform pip failed ({exc}); retrying without platform pinning")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "-r", str(req), "-t", str(dest),
            "--quiet", "--no-compile", "--upgrade",
        ])


def package_zip() -> bytes:
    with tempfile.TemporaryDirectory(prefix="atrium-input-agent-") as tmp:
        build_dir = Path(tmp)
        # 1. Copy the .py sources
        for src in LAMBDA_DIR.rglob("*.py"):
            rel = src.relative_to(LAMBDA_DIR)
            dst = build_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        # 2. Install runtime deps alongside the sources
        _pip_install_requirements(build_dir)

        # 3. Zip the build dir
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for path in build_dir.rglob("*"):
                if path.is_file():
                    arc = path.relative_to(build_dir).as_posix()
                    # Trim pip metadata noise to keep the zip small.
                    if "/__pycache__/" in f"/{arc}/" or arc.endswith(".pyc"):
                        continue
                    z.write(path, arc)

    buf.seek(0)
    data = buf.read()
    print(f"{OK} Packaged {len(data)} bytes from {LAMBDA_DIR} (+ vendored deps)")
    return data


def _load_calendar_env() -> dict[str, str]:
    """Read local Google Calendar config and shape it for Lambda env vars.

    Looks for:
      - local/credentials/service-account.json  (gitignored)  → GOOGLE_SA_JSON_B64
      - local/.env CALENDAR_ID_CREW_ZH_1                       → GOOGLE_CALENDAR_ID
    Silently skips missing pieces so deploys still work without calendar wiring.
    """
    env: dict[str, str] = {}
    sa_path = ROOT / "local" / "credentials" / "service-account.json"
    if sa_path.exists():
        try:
            raw = sa_path.read_bytes()
            env["GOOGLE_SA_JSON_B64"] = base64.b64encode(raw).decode("ascii")
            print(f"{OK} Bundled Google service-account JSON via env var ({len(raw)} bytes)")
        except Exception as exc:
            print(f"{WAIT} skipped service-account.json: {exc}")
    else:
        print(f"{WAIT} no service-account.json at {sa_path} — calendar tool will soft-fail")

    dotenv_path = ROOT / "local" / ".env"
    if dotenv_path.exists():
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key == "CALENDAR_ID_CREW_ZH_1" and value and "GOOGLE_CALENDAR_ID" not in env:
                env["GOOGLE_CALENDAR_ID"] = value
                print(f"{OK} GOOGLE_CALENDAR_ID set from local/.env")

    env.setdefault("ATRIUM_TIMEZONE", "Europe/Zurich")
    return env


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
    env_vars = _load_calendar_env()
    if function_exists(FUNCTION_NAME):
        print(f"{WAIT} Updating existing function {FUNCTION_NAME} ...")
        lc.update_function_code(FunctionName=FUNCTION_NAME, ZipFile=zip_bytes)
        wait_active(FUNCTION_NAME)
        # Merge so we don't clobber other env vars set out-of-band.
        existing = (lc.get_function_configuration(FunctionName=FUNCTION_NAME)
                    .get("Environment", {}).get("Variables", {})) or {}
        merged = {**existing, **env_vars}
        lc.update_function_configuration(
            FunctionName=FUNCTION_NAME,
            Runtime=RUNTIME,
            Handler=HANDLER,
            Role=role_arn,
            Timeout=TIMEOUT_S,
            MemorySize=MEMORY_MB,
            Environment={"Variables": merged},
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
                    Description="Atrium Input Agent — Lex CodeHook (Bedrock + Google Calendar)",
                    Environment={"Variables": env_vars} if env_vars else {"Variables": {}},
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
    print("    Architecture: Connect contact flow -> Lex GetCustomerInput -> Lambda CodeHook (this fn)")
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

    grant_lex_invoke(function_arn)

    print("\n=== Done. ===")
    print(f"     Function ARN: {function_arn}")
    print(f"     Logs: aws logs tail /aws/lambda/{FUNCTION_NAME} --follow (or use scripts/tail_logs.py)")
    print("     Next: run scripts/provision_lex.py, then scripts/provision_connect.py.")
    return 0


def grant_lex_invoke(function_arn: str) -> None:
    """Idempotently grant Amazon Lex permission to invoke this Lambda."""
    lc = lambda_client()
    statement_id = "AllowLexInvoke"
    try:
        lc.add_permission(
            FunctionName=FUNCTION_NAME,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal="lexv2.amazonaws.com",
        )
        print(f"{OK} Granted lexv2.amazonaws.com -> lambda:InvokeFunction")
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code in ("ResourceConflictException",):
            print(f"{OK} Lex invoke permission already present (sid={statement_id})")
            return
        raise


if __name__ == "__main__":
    sys.exit(main())
