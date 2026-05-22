"""Stage 3 — provision the Lex V2 bot that drives the Atrium phone call.

Creates (or reuses):
  1. IAM service-linked role for Lex V2 (if missing in the account)
  2. Lex V2 bot 'atrium-input-agent'
  3. en_US locale with one CollectBooking intent (6 required slots)
  4. The Lambda code hook attachment on that locale (input_agent Lambda)
  5. A bot alias 'live' for the current bot version

Reads scripts/.connect_state.json from prior stages and writes back:
  - LexBotId
  - LexBotAliasId
  - LexLocaleId

Idempotent — re-running won't duplicate resources.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import boto3
from botocore.exceptions import ClientError

from aws_helpers import get_account_id, iam, region

STATE_FILE = Path(__file__).parent / ".connect_state.json"
BOT_NAME = "atrium-input-agent"
LOCALE_ID = "en_US"
ALIAS_NAME = "live"
INTENT_NAME = "CollectBooking"
SLOT_NAMES = ("when", "what", "area", "rooms", "urgency", "email")
LEX_ROLE_NAME = "AWSServiceRoleForLexV2Bots"
LEX_ROLE_ARN = f"arn:aws:iam::{{account_id}}:role/aws-service-role/lexv2.amazonaws.com/{LEX_ROLE_NAME}"

OK = "[OK]"
WAIT = "[..]"
FAIL = "[FAIL]"


def load_state() -> dict:
    if not STATE_FILE.exists():
        raise SystemExit(f"{FAIL} Run provision_connect.py + deploy_lambda.py first")
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def lex() -> "boto3.client":
    return boto3.client("lexv2-models", region_name=region())


def ensure_service_linked_role() -> str:
    arn = LEX_ROLE_ARN.format(account_id=get_account_id())
    i = iam()
    try:
        i.get_role(RoleName=LEX_ROLE_NAME)
        print(f"{OK} Lex service-linked role exists")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        print(f"{WAIT} Creating Lex service-linked role ...")
        i.create_service_linked_role(AWSServiceName="lexv2.amazonaws.com")
    return arn


def find_bot(name: str) -> str | None:
    client = lex()
    next_token: str | None = None
    while True:
        kwargs = {"maxResults": 50}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = client.list_bots(**kwargs)
        for bot in resp.get("botSummaries", []):
            if bot["botName"] == name:
                return bot["botId"]
        next_token = resp.get("nextToken")
        if not next_token:
            return None


def wait_bot(bot_id: str, target: str = "Available") -> None:
    for _ in range(60):
        d = lex().describe_bot(botId=bot_id)
        status = d.get("botStatus")
        if status == target:
            return
        if status in {"Failed", "Deleting"}:
            raise RuntimeError(f"bot {bot_id} status={status}")
        time.sleep(3)
    raise RuntimeError(f"bot {bot_id} did not reach {target}")


def wait_locale(bot_id: str, target: str = "Built") -> None:
    for _ in range(60):
        d = lex().describe_bot_locale(botId=bot_id, botVersion="DRAFT", localeId=LOCALE_ID)
        status = d.get("botLocaleStatus")
        if status == target:
            return
        if status in {"Failed", "Deleting"}:
            raise RuntimeError(f"locale status={status}")
        time.sleep(3)
    raise RuntimeError(f"locale did not reach {target}")


def ensure_bot(role_arn: str) -> str:
    bot_id = find_bot(BOT_NAME)
    if bot_id:
        print(f"{OK} Bot exists: {bot_id}")
        return bot_id
    print(f"{WAIT} Creating bot {BOT_NAME} ...")
    resp = lex().create_bot(
        botName=BOT_NAME,
        description="Atrium voice intake bot",
        roleArn=role_arn,
        dataPrivacy={"childDirected": False},
        idleSessionTTLInSeconds=600,
        botType="Bot",
    )
    bot_id = resp["botId"]
    wait_bot(bot_id)
    print(f"{OK} Bot created: {bot_id}")
    return bot_id


def ensure_locale(bot_id: str) -> None:
    try:
        lex().describe_bot_locale(botId=bot_id, botVersion="DRAFT", localeId=LOCALE_ID)
        print(f"{OK} Locale {LOCALE_ID} exists")
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    print(f"{WAIT} Creating locale {LOCALE_ID} ...")
    lex().create_bot_locale(
        botId=bot_id,
        botVersion="DRAFT",
        localeId=LOCALE_ID,
        nluIntentConfidenceThreshold=0.4,
        voiceSettings={"voiceId": "Joanna", "engine": "neural"},
    )
    wait_locale(bot_id, "NotBuilt")


def _find_intent(bot_id: str, name: str) -> str | None:
    resp = lex().list_intents(botId=bot_id, botVersion="DRAFT", localeId=LOCALE_ID)
    for intent in resp.get("intentSummaries", []):
        if intent["intentName"] == name:
            return intent["intentId"]
    return None


def ensure_intent(bot_id: str) -> str:
    intent_id = _find_intent(bot_id, INTENT_NAME)
    if intent_id:
        print(f"{OK} Intent {INTENT_NAME} exists: {intent_id}")
        return intent_id
    print(f"{WAIT} Creating intent {INTENT_NAME} ...")
    resp = lex().create_intent(
        botId=bot_id,
        botVersion="DRAFT",
        localeId=LOCALE_ID,
        intentName=INTENT_NAME,
        description="Collects the six booking slots for the Atrium voice agent.",
        dialogCodeHook={"enabled": True},
        fulfillmentCodeHook={"enabled": True},
        sampleUtterances=[
            {"utterance": "I'd like to book a cleaning"},
            {"utterance": "I need to schedule a cleaning"},
            {"utterance": "Can you clean my place"},
            {"utterance": "I need a quote"},
        ],
    )
    return resp["intentId"]


def ensure_slots(bot_id: str, intent_id: str) -> dict[str, str]:
    """Create the six required slots; reuses existing IDs by slot name."""
    builtin_type = "AMAZON.FreeFormInput"
    existing = lex().list_slots(
        botId=bot_id, botVersion="DRAFT", localeId=LOCALE_ID, intentId=intent_id
    )
    by_name = {s["slotName"]: s["slotId"] for s in existing.get("slotSummaries", [])}
    slot_ids: dict[str, str] = {}
    for name in SLOT_NAMES:
        if name in by_name:
            slot_ids[name] = by_name[name]
            continue
        print(f"{WAIT} Creating slot {name} ...")
        resp = lex().create_slot(
            botId=bot_id,
            botVersion="DRAFT",
            localeId=LOCALE_ID,
            intentId=intent_id,
            slotName=name,
            slotTypeId=builtin_type,
            valueElicitationSetting={
                "slotConstraint": "Required",
                "promptSpecification": {
                    "messageGroups": [
                        {
                            "message": {
                                "plainTextMessage": {
                                    "value": f"Please tell me the {name}.",
                                }
                            }
                        }
                    ],
                    "maxRetries": 2,
                    "allowInterrupt": True,
                },
            },
        )
        slot_ids[name] = resp["slotId"]
    return slot_ids


def set_slot_priorities(bot_id: str, intent_id: str, slot_ids: dict[str, str]) -> None:
    """Lex refuses to build the locale until every slot has an explicit priority."""
    priorities = [
        {"priority": idx + 1, "slotId": slot_ids[name]}
        for idx, name in enumerate(SLOT_NAMES)
        if name in slot_ids
    ]
    intent = lex().describe_intent(
        botId=bot_id, botVersion="DRAFT", localeId=LOCALE_ID, intentId=intent_id
    )
    print(f"{WAIT} Setting slot priorities ...")
    lex().update_intent(
        botId=bot_id,
        botVersion="DRAFT",
        localeId=LOCALE_ID,
        intentId=intent_id,
        intentName=intent.get("intentName", INTENT_NAME),
        description=intent.get("description", ""),
        sampleUtterances=intent.get("sampleUtterances") or [],
        dialogCodeHook=intent.get("dialogCodeHook") or {"enabled": True},
        fulfillmentCodeHook=intent.get("fulfillmentCodeHook") or {"enabled": True},
        slotPriorities=priorities,
    )
    print(f"{OK} Slot priorities set")


def attach_lambda(bot_id: str, lambda_arn: str) -> None:
    print(f"{WAIT} Wiring Lambda code hook to locale ...")
    lex().update_bot_alias(
        botAliasId="TSTALIASID",  # built-in draft alias
        botAliasName="TestBotAlias",
        botId=bot_id,
        botVersion="DRAFT",
        botAliasLocaleSettings={
            LOCALE_ID: {
                "enabled": True,
                "codeHookSpecification": {
                    "lambdaCodeHook": {
                        "lambdaARN": lambda_arn,
                        "codeHookInterfaceVersion": "1.0",
                    }
                },
            }
        },
    )
    print(f"{OK} Lambda wired to draft alias")


def build_locale(bot_id: str) -> None:
    print(f"{WAIT} Building locale {LOCALE_ID} ...")
    lex().build_bot_locale(botId=bot_id, botVersion="DRAFT", localeId=LOCALE_ID)
    wait_locale(bot_id, "Built")
    print(f"{OK} Locale built")


def create_bot_version(bot_id: str) -> str:
    print(f"{WAIT} Creating numbered bot version ...")
    resp = lex().create_bot_version(
        botId=bot_id,
        botVersionLocaleSpecification={
            LOCALE_ID: {"sourceBotVersion": "DRAFT"},
        },
    )
    version = resp["botVersion"]
    # Poll until the version is Available.
    for _ in range(60):
        d = lex().describe_bot_version(botId=bot_id, botVersion=version)
        status = d.get("botStatus")
        if status == "Available":
            print(f"{OK} Bot version {version} available")
            return version
        if status in {"Failed", "Deleting"}:
            raise RuntimeError(f"bot version {version} status={status}")
        time.sleep(3)
    raise RuntimeError(f"bot version {version} did not reach Available")


def ensure_alias(bot_id: str, lambda_arn: str, bot_version: str) -> str:
    aliases = lex().list_bot_aliases(botId=bot_id).get("botAliasSummaries", [])
    for alias in aliases:
        if alias["botAliasName"] == ALIAS_NAME:
            print(f"{OK} Alias {ALIAS_NAME} exists: {alias['botAliasId']}")
            return alias["botAliasId"]
    print(f"{WAIT} Creating alias {ALIAS_NAME} -> version {bot_version} ...")
    resp = lex().create_bot_alias(
        botAliasName=ALIAS_NAME,
        botId=bot_id,
        botVersion=bot_version,
        botAliasLocaleSettings={
            LOCALE_ID: {
                "enabled": True,
                "codeHookSpecification": {
                    "lambdaCodeHook": {
                        "lambdaARN": lambda_arn,
                        "codeHookInterfaceVersion": "1.0",
                    }
                },
            }
        },
    )
    return resp["botAliasId"]


def allow_lex_invoke(function_name: str, bot_id: str, alias_id: str) -> None:
    """Give lexv2.amazonaws.com permission to invoke our Lambda code hook.

    Lex V2 rejects code-hook invocations with AccessDenied unless the
    Lambda's resource policy allows it. The source ARN must scope to the
    specific bot alias so other bots can't call our Lambda.
    """
    lc = boto3.client("lambda", region_name=region())
    statement_id = f"AllowLexInvoke-{bot_id[:8]}-{alias_id[:8]}"
    source_arn = f"arn:aws:lex:{region()}:{get_account_id()}:bot-alias/{bot_id}/{alias_id}"
    try:
        lc.add_permission(
            FunctionName=function_name,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal="lexv2.amazonaws.com",
            SourceArn=source_arn,
        )
        print(f"{OK} Lex can invoke Lambda (SourceArn={source_arn})")
    except ClientError as e:
        if e.response["Error"].get("Code") == "ResourceConflictException":
            print(f"{OK} Lex Lambda permission already exists")
        else:
            raise


def main() -> int:
    state = load_state()
    lambda_arn = state.get("LambdaFunctionArn")
    if not lambda_arn:
        raise SystemExit(f"{FAIL} Run deploy_lambda.py first; LambdaFunctionArn missing")

    role_arn = ensure_service_linked_role()
    bot_id = ensure_bot(role_arn)
    ensure_locale(bot_id)
    intent_id = ensure_intent(bot_id)
    slot_ids = ensure_slots(bot_id, intent_id)
    set_slot_priorities(bot_id, intent_id, slot_ids)
    attach_lambda(bot_id, lambda_arn)
    build_locale(bot_id)
    bot_version = create_bot_version(bot_id)
    alias_id = ensure_alias(bot_id, lambda_arn, bot_version)

    function_name = state.get("LambdaFunctionName", "atrium-input-agent")
    allow_lex_invoke(function_name, bot_id, alias_id)
    # Lex's draft test alias has the well-known ID 'TSTALIASID'; let Lex use
    # it too while we are iterating (covers the bot console "Test" button).
    allow_lex_invoke(function_name, bot_id, "TSTALIASID")

    state.update({
        "LexBotId": bot_id,
        "LexBotAliasId": alias_id,
        "LexLocaleId": LOCALE_ID,
        "LexIntentId": intent_id,
        "LexSlotIds": slot_ids,
    })
    save_state(state)
    print(f"\n=== Lex bot ready: botId={bot_id} aliasId={alias_id} ===")
    print("Next: rerun deploy_lambda.py to rewrite the contact flow with the Lex GetCustomerInput block.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
