"""Provision the Amazon Lex V2 bot that fronts the Connect contact flow.

Idempotent. Creates (or reuses):
  1. IAM role  atrium-lex-bot-role            (assumable by lexv2.amazonaws.com)
  2. Lex bot   atrium-receptionist            (FallbackIntent only)
  3. Locale    en_US                          (Polly Joanna voice; tweak per-tenant later)
  4. Build     the locale                     (NLU model compile)
  5. Version   numeric snapshot
  6. Alias     atrium-live                    (CodeHook -> atrium-input-agent Lambda)

The Lambda's resource policy is already configured by deploy_lambda.py to allow
lexv2.amazonaws.com to invoke it.

Outputs `.lex_state.json` consumed by provision_connect.py.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from botocore.exceptions import ClientError

from aws_helpers import get_account_id, iam, lex_models, region

STATE_FILE = Path(__file__).parent / ".lex_state.json"
DEPLOY_STATE_FILE = Path(__file__).parent / ".deploy_state.json"

BOT_NAME = "atrium-receptionist"
BOT_ROLE_NAME = "atrium-lex-bot-role"
LOCALE_ID = "en_US"
LOCALE_VOICE_ID = "Joanna"
ALIAS_NAME = "atrium-live"
LAMBDA_FUNCTION_NAME = "atrium-input-agent"

OK = "[OK]"
WAIT = "[..]"
FAIL = "[FAIL]"

LEX_TRUST = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lexv2.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}

LEX_INLINE_POLICY = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Action": ["polly:SynthesizeSpeech", "comprehend:DetectSentiment"],
        "Resource": "*",
    }],
}


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_lambda_arn() -> str:
    if not DEPLOY_STATE_FILE.exists():
        raise SystemExit("Run scripts/deploy_lambda.py first — .deploy_state.json missing.")
    state = json.loads(DEPLOY_STATE_FILE.read_text())
    arn = state.get("LambdaFunctionArn")
    if not arn:
        raise SystemExit("LambdaFunctionArn missing from .deploy_state.json")
    return arn


def ensure_bot_role() -> str:
    i = iam()
    try:
        r = i.get_role(RoleName=BOT_ROLE_NAME)
        print(f"{OK} Lex bot role exists: {r['Role']['Arn']}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        print(f"{WAIT} Creating Lex bot role {BOT_ROLE_NAME} ...")
        r = i.create_role(
            RoleName=BOT_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(LEX_TRUST),
            Description="Execution role for Atrium Lex V2 receptionist bot",
        )
        print(f"{OK} Lex bot role created: {r['Role']['Arn']}")
    i.put_role_policy(
        RoleName=BOT_ROLE_NAME,
        PolicyName="atrium-lex-runtime",
        PolicyDocument=json.dumps(LEX_INLINE_POLICY),
    )
    return r["Role"]["Arn"]


def find_bot_by_name(name: str) -> str | None:
    lm = lex_models()
    next_token: str | None = None
    while True:
        kwargs = {"maxResults": 50}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = lm.list_bots(**kwargs)
        for b in resp.get("botSummaries", []):
            if b.get("botName") == name:
                return b["botId"]
        next_token = resp.get("nextToken")
        if not next_token:
            return None


def wait_bot_status(bot_id: str, target: str | tuple[str, ...], timeout: int = 120) -> str:
    targets = (target,) if isinstance(target, str) else target
    for _ in range(timeout):
        d = lex_models().describe_bot(botId=bot_id)
        status = d.get("botStatus")
        if status in targets:
            return status
        if status == "Failed":
            raise SystemExit(f"Bot {bot_id} reached Failed status")
        time.sleep(2)
    raise SystemExit(f"Bot {bot_id} did not reach {targets} within {timeout*2}s")


def wait_locale_status(bot_id: str, version: str, target: str, timeout: int = 240) -> None:
    for _ in range(timeout):
        d = lex_models().describe_bot_locale(botId=bot_id, botVersion=version, localeId=LOCALE_ID)
        status = d.get("botLocaleStatus")
        if status == target:
            return
        if status in ("Failed", "ReadyExpressTestFailed"):
            raise SystemExit(f"Locale build failed: {d.get('failureReasons')}")
        time.sleep(2)
    raise SystemExit(f"Locale did not reach {target} within {timeout*2}s")


def ensure_bot(role_arn: str) -> str:
    lm = lex_models()
    existing = find_bot_by_name(BOT_NAME)
    if existing:
        print(f"{OK} Bot exists: {existing}")
        return existing
    print(f"{WAIT} Creating bot {BOT_NAME} ...")
    resp = lm.create_bot(
        botName=BOT_NAME,
        description="Atrium receptionist — routes all utterances to the Input Agent Lambda.",
        roleArn=role_arn,
        dataPrivacy={"childDirected": False},
        idleSessionTTLInSeconds=300,
    )
    bot_id = resp["botId"]
    print(f"     bot_id = {bot_id}")
    wait_bot_status(bot_id, "Available")
    return bot_id


def ensure_locale(bot_id: str) -> None:
    lm = lex_models()
    try:
        lm.describe_bot_locale(botId=bot_id, botVersion="DRAFT", localeId=LOCALE_ID)
        print(f"{OK} Locale {LOCALE_ID} exists on DRAFT")
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    print(f"{WAIT} Creating locale {LOCALE_ID} ...")
    lm.create_bot_locale(
        botId=bot_id,
        botVersion="DRAFT",
        localeId=LOCALE_ID,
        nluIntentConfidenceThreshold=0.40,
        voiceSettings={"voiceId": LOCALE_VOICE_ID, "engine": "neural"},
    )
    wait_locale_status(bot_id, "DRAFT", "NotBuilt")


CATCHALL_INTENT_NAME = "CatchAllIntent"

# Broad utterances that cover typical phone-call openings. Lex needs at least
# one custom intent with utterances for the locale to build; whichever intent
# fires, the Lambda receives the raw inputTranscript and drives the dialog.
CATCHALL_UTTERANCES = [
    "hello",
    "hi there",
    "i need help",
    "i need a cleaning",
    "i want to book a cleaning",
    "how much does it cost",
    "what are your prices",
    "yes",
    "no",
    "thank you",
    "okay",
    "next friday",
]


def _find_intent_id(bot_id: str, intent_name: str) -> str | None:
    lm = lex_models()
    next_token: str | None = None
    while True:
        kwargs: dict = {"botId": bot_id, "botVersion": "DRAFT", "localeId": LOCALE_ID, "maxResults": 50}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = lm.list_intents(**kwargs)
        for i in resp.get("intentSummaries", []):
            if i["intentName"] == intent_name:
                return i["intentId"]
        next_token = resp.get("nextToken")
        if not next_token:
            return None


def ensure_intents(bot_id: str) -> None:
    """Confirm FallbackIntent exists and create a CatchAllIntent that satisfies
    Lex V2's "at least one custom intent with utterances" build requirement.

    Both intents share the alias-level codeHookSpecification, so every caller
    turn — whether matched as CatchAllIntent or FallbackIntent — invokes our
    Lambda. We also flip on per-intent dialogCodeHook + fulfillmentCodeHook
    flags so the hook actually fires.
    """
    lm = lex_models()

    fallback_id = _find_intent_id(bot_id, "FallbackIntent")
    if not fallback_id:
        raise SystemExit("FallbackIntent unexpectedly missing")
    print(f"{OK} FallbackIntent present: {fallback_id}")
    _enable_hooks_on_intent(bot_id, fallback_id, "FallbackIntent")

    catchall_id = _find_intent_id(bot_id, CATCHALL_INTENT_NAME)
    sample_utterances = [{"utterance": u} for u in CATCHALL_UTTERANCES]
    if catchall_id:
        print(f"{WAIT} Updating existing {CATCHALL_INTENT_NAME} ({catchall_id}) utterances ...")
        lm.update_intent(
            intentId=catchall_id,
            intentName=CATCHALL_INTENT_NAME,
            description="Catches typical phone-call openings; routes via the Lambda hook.",
            sampleUtterances=sample_utterances,
            dialogCodeHook={"enabled": True},
            fulfillmentCodeHook={"enabled": True},
            botId=bot_id,
            botVersion="DRAFT",
            localeId=LOCALE_ID,
        )
        print(f"{OK} {CATCHALL_INTENT_NAME} updated")
    else:
        print(f"{WAIT} Creating {CATCHALL_INTENT_NAME} ...")
        resp = lm.create_intent(
            intentName=CATCHALL_INTENT_NAME,
            description="Catches typical phone-call openings; routes via the Lambda hook.",
            sampleUtterances=sample_utterances,
            dialogCodeHook={"enabled": True},
            fulfillmentCodeHook={"enabled": True},
            botId=bot_id,
            botVersion="DRAFT",
            localeId=LOCALE_ID,
        )
        catchall_id = resp["intentId"]
        print(f"{OK} {CATCHALL_INTENT_NAME} created: {catchall_id}")


def _enable_hooks_on_intent(bot_id: str, intent_id: str, intent_name: str) -> None:
    """Idempotently flip dialog + fulfillment code hooks on an existing intent."""
    lm = lex_models()
    described = lm.describe_intent(
        intentId=intent_id, botId=bot_id, botVersion="DRAFT", localeId=LOCALE_ID,
    )
    dialog_enabled = (described.get("dialogCodeHook") or {}).get("enabled", False)
    fulfill_enabled = (described.get("fulfillmentCodeHook") or {}).get("enabled", False)
    if dialog_enabled and fulfill_enabled:
        print(f"{OK} {intent_name} already has code hooks enabled")
        return
    # update_intent requires the full intent definition; carry forward whatever
    # Lex returned plus the hook flags.
    update_kwargs = {
        "intentId": intent_id,
        "intentName": described["intentName"],
        "botId": bot_id,
        "botVersion": "DRAFT",
        "localeId": LOCALE_ID,
        "dialogCodeHook": {"enabled": True},
        "fulfillmentCodeHook": {"enabled": True},
    }
    for optional_field in (
        "description", "parentIntentSignature", "sampleUtterances",
        "intentConfirmationSetting", "intentClosingSetting",
        "inputContexts", "outputContexts", "kendraConfiguration",
        "slotPriorities", "initialResponseSetting", "qnAIntentConfiguration",
    ):
        if optional_field in described and described[optional_field]:
            update_kwargs[optional_field] = described[optional_field]
    lm.update_intent(**update_kwargs)
    print(f"{OK} {intent_name} hooks enabled")


def build_locale(bot_id: str) -> None:
    print(f"{WAIT} Building DRAFT locale {LOCALE_ID} ...")
    lex_models().build_bot_locale(botId=bot_id, botVersion="DRAFT", localeId=LOCALE_ID)
    wait_locale_status(bot_id, "DRAFT", "Built")
    print(f"{OK} Locale built")


def create_version(bot_id: str) -> str:
    lm = lex_models()
    print(f"{WAIT} Creating bot version ...")
    resp = lm.create_bot_version(
        botId=bot_id,
        botVersionLocaleSpecification={
            LOCALE_ID: {"sourceBotVersion": "DRAFT"},
        },
    )
    version = resp["botVersion"]
    print(f"     version = {version}, polling status ...")
    for attempt in range(60):
        try:
            d = lm.describe_bot_version(botId=bot_id, botVersion=version)
        except ClientError as e:
            if e.response["Error"].get("Code") == "ResourceNotFoundException":
                # Propagation delay between create + describe — retry quietly.
                time.sleep(2)
                continue
            raise
        status = d.get("botStatus")
        if status == "Available":
            print(f"{OK} Version {version} ready")
            return version
        if status == "Failed":
            raise SystemExit(f"Version {version} failed")
        time.sleep(2)
    raise SystemExit("Version did not become Available")


def ensure_alias(bot_id: str, version: str, lambda_arn: str) -> str:
    lm = lex_models()
    existing_id: str | None = None
    next_token: str | None = None
    while existing_id is None:
        kwargs: dict = {"botId": bot_id, "maxResults": 50}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = lm.list_bot_aliases(**kwargs)
        for a in resp.get("botAliasSummaries", []):
            if a["botAliasName"] == ALIAS_NAME:
                existing_id = a["botAliasId"]
                break
        next_token = resp.get("nextToken")
        if not next_token:
            break

    code_hook_specification = {
        "lambdaCodeHook": {
            "lambdaARN": lambda_arn,
            "codeHookInterfaceVersion": "1.0",
        }
    }
    alias_locale_settings = {
        LOCALE_ID: {
            "enabled": True,
            "codeHookSpecification": code_hook_specification,
        }
    }
    if existing_id:
        print(f"{WAIT} Updating existing alias {ALIAS_NAME} ({existing_id}) -> version {version}")
        lm.update_bot_alias(
            botAliasId=existing_id,
            botAliasName=ALIAS_NAME,
            botVersion=version,
            botId=bot_id,
            botAliasLocaleSettings=alias_locale_settings,
        )
        return existing_id

    print(f"{WAIT} Creating alias {ALIAS_NAME} -> version {version}")
    resp = lm.create_bot_alias(
        botAliasName=ALIAS_NAME,
        description="Live alias used by Connect contact flow",
        botVersion=version,
        botId=bot_id,
        botAliasLocaleSettings=alias_locale_settings,
    )
    return resp["botAliasId"]


def main() -> int:
    print(f"=== Provisioning Lex bot (region={region()}, account={get_account_id()}) ===")
    lambda_arn = get_lambda_arn()
    print(f"     Lambda CodeHook target: {lambda_arn}")

    role_arn = ensure_bot_role()
    bot_id = ensure_bot(role_arn)
    ensure_locale(bot_id)
    ensure_intents(bot_id)
    build_locale(bot_id)
    version = create_version(bot_id)
    alias_id = ensure_alias(bot_id, version, lambda_arn)

    bot_alias_arn = (
        f"arn:aws:lex:{region()}:{get_account_id()}:bot-alias/{bot_id}/{alias_id}"
    )

    state = {
        "BotId": bot_id,
        "BotName": BOT_NAME,
        "BotAliasId": alias_id,
        "BotAliasName": ALIAS_NAME,
        "BotAliasArn": bot_alias_arn,
        "BotVersion": version,
        "LocaleId": LOCALE_ID,
        "LambdaCodeHookArn": lambda_arn,
        "BotRoleArn": role_arn,
        "Region": region(),
    }
    save_state(state)
    print(f"\n=== Done. State written to {STATE_FILE} ===")
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
