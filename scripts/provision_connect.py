"""Provision the Amazon Connect side of the Atrium voice path.

Idempotent. Reuses an existing instance + claimed number if present, otherwise
creates them. The contact flow `atrium-inbound` is (re)written to a Lex-driven
flow that hands the entire dialog to the `atrium-receptionist` Lex bot.

Prereqs:
  1. scripts/deploy_lambda.py   (creates the Input Agent Lambda + Lex invoke perm)
  2. scripts/provision_lex.py   (creates the bot + alias, writes .lex_state.json)

The contact flow looks like:

    Start -> UpdateContactAttributes(companyId=glanz-ag, callId=$.ContactId)
          -> ConnectParticipantWithLexBot(BotAliasArn from .lex_state.json)
          -> DisconnectParticipant

The Lex block is what makes the call actually listen + speak. The Lambda
CodeHook drives every turn (Bedrock Claude Sonnet 4.6 + tool-use).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from botocore.exceptions import ClientError

from aws_helpers import connect_client, get_account_id, region

STATE_FILE = Path(__file__).parent / ".connect_state.json"
LEX_STATE_FILE = Path(__file__).parent / ".lex_state.json"

INSTANCE_ALIAS = "atrium-demo"
CONTACT_FLOW_NAME = "atrium-inbound"
DEFAULT_COMPANY_ID = "glanz-ag"
GREETING = "Hi, this is Sarah from Glanz AG. How can I help you today?"
GOODBYE = "Thanks for calling Glanz AG. Have a great day!"
ERROR_MSG = "Sorry, something went wrong on our side. Please call back later."
TTS_VOICE = "Matthew"  # Polly generative voice for greeting/farewell

OK = "[OK]"
WAIT = "[..]"
FAIL = "[FAIL]"


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def load_lex_state() -> dict:
    if not LEX_STATE_FILE.exists():
        raise SystemExit("Run scripts/provision_lex.py first — .lex_state.json missing.")
    return json.loads(LEX_STATE_FILE.read_text())


def find_instance_by_alias(alias: str) -> dict | None:
    cc = connect_client()
    paginator = cc.get_paginator("list_instances")
    for page in paginator.paginate():
        for inst in page["InstanceSummaryList"]:
            if inst.get("InstanceAlias") == alias:
                return inst
    return None


def ensure_instance() -> dict:
    found = find_instance_by_alias(INSTANCE_ALIAS)
    if found:
        print(f"{OK} Connect instance reused: {found['Id']} (status={found.get('InstanceStatus')})")
        return found
    raise SystemExit(
        f"No Connect instance with alias '{INSTANCE_ALIAS}'. "
        "Create one in the Connect console (one-time manual step) or restore "
        "the create_instance flow from git history if your workshop role allows it."
    )


def find_claimed_number(instance_arn: str) -> dict | None:
    cc = connect_client()
    paginator = cc.get_paginator("list_phone_numbers_v2")
    for page in paginator.paginate(TargetArn=instance_arn):
        nums = page.get("ListPhoneNumbersSummaryList", [])
        if nums:
            return nums[0]
    return None


def find_flow(instance_id: str, name: str) -> str | None:
    cc = connect_client()
    paginator = cc.get_paginator("list_contact_flows")
    for page in paginator.paginate(InstanceId=instance_id, ContactFlowTypes=["CONTACT_FLOW"]):
        for f in page["ContactFlowSummaryList"]:
            if f["Name"] == name:
                return f["Id"]
    return None


def build_flow_content(
    bot_id: str,
    bot_alias_id: str,
    aws_region: str,
    company_id: str = DEFAULT_COMPANY_ID,
) -> dict:
    """Build the Lex-driven contact flow JSON.

    Connect's contact-flow JSON uses the legacy `LexBot` parameter key even for
    Lex V2 bots, with `{Name=botId, Region, Alias=botAliasId}`. The Lex V2
    block, despite the legacy name, fully supports our bot.
    """
    return {
        "Version": "2019-10-30",
        "StartAction": "set_voice",
        "Metadata": {
            "entryPointPosition": {"x": 40, "y": 40},
            "ActionMetadata": {
                "set_voice": {"position": {"x": 200, "y": 40}},
                "set_attrs": {"position": {"x": 380, "y": 40}},
                "engage": {"position": {"x": 560, "y": 40}},
                "goodbye": {"position": {"x": 740, "y": 40}},
                "err_msg": {"position": {"x": 560, "y": 240}},
                "disconnect": {"position": {"x": 920, "y": 40}},
            },
        },
        "Actions": [
            {
                "Identifier": "set_voice",
                "Type": "UpdateContactTextToSpeechVoice",
                "Parameters": {
                    "TextToSpeechEngine": "generative",
                    "TextToSpeechVoice": TTS_VOICE,
                },
                "Transitions": {
                    "NextAction": "set_attrs",
                    "Errors": [],
                    "Conditions": [],
                },
            },
            {
                "Identifier": "set_attrs",
                "Type": "UpdateContactAttributes",
                "Parameters": {
                    "Attributes": {"companyId": company_id},
                    "TargetContact": "Current",
                },
                "Transitions": {
                    "NextAction": "engage",
                    "Errors": [
                        {"NextAction": "engage", "ErrorType": "NoMatchingError"},
                    ],
                    "Conditions": [],
                },
            },
            {
                "Identifier": "engage",
                "Type": "ConnectParticipantWithLexBot",
                "Parameters": {
                    "Text": GREETING,
                    "LexBot": {
                        "Name": bot_id,
                        "Region": aws_region,
                        "Alias": bot_alias_id,
                    },
                },
                "Transitions": {
                    "NextAction": "goodbye",
                    "Errors": [
                        {"NextAction": "err_msg", "ErrorType": "NoMatchingError"},
                        {"NextAction": "goodbye", "ErrorType": "NoMatchingCondition"},
                    ],
                    "Conditions": [],
                },
            },
            {
                "Identifier": "goodbye",
                "Type": "MessageParticipant",
                "Parameters": {"Text": GOODBYE},
                "Transitions": {
                    "NextAction": "disconnect",
                    "Errors": [],
                    "Conditions": [],
                },
            },
            {
                "Identifier": "err_msg",
                "Type": "MessageParticipant",
                "Parameters": {"Text": ERROR_MSG},
                "Transitions": {
                    "NextAction": "disconnect",
                    "Errors": [],
                    "Conditions": [],
                },
            },
            {
                "Identifier": "disconnect",
                "Type": "DisconnectParticipant",
                "Parameters": {},
                "Transitions": {},
            },
        ],
    }


def upsert_contact_flow(instance_id: str, content: dict) -> str:
    cc = connect_client()
    flow_id = find_flow(instance_id, CONTACT_FLOW_NAME)
    content_str = json.dumps(content)
    if flow_id:
        print(f"{WAIT} Updating existing contact flow {CONTACT_FLOW_NAME} ({flow_id}) ...")
        cc.update_contact_flow_content(
            InstanceId=instance_id,
            ContactFlowId=flow_id,
            Content=content_str,
        )
        print(f"{OK} Contact flow content updated")
        return flow_id
    print(f"{WAIT} Creating contact flow {CONTACT_FLOW_NAME} ...")
    resp = cc.create_contact_flow(
        InstanceId=instance_id,
        Name=CONTACT_FLOW_NAME,
        Type="CONTACT_FLOW",
        Description="Atrium inbound — hands the call to the Lex receptionist bot.",
        Content=content_str,
    )
    return resp["ContactFlowId"]


def associate_lex_bot(instance_id: str, alias_arn: str) -> None:
    cc = connect_client()
    try:
        cc.associate_bot(InstanceId=instance_id, LexV2Bot={"AliasArn": alias_arn})
        print(f"{OK} Associated Lex bot alias with Connect instance")
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code in ("ResourceConflictException", "DuplicateResourceException"):
            print(f"{OK} Lex bot already associated with Connect instance")
            return
        raise


def map_number_to_flow(phone_number_id: str, instance_id: str, flow_id: str) -> None:
    cc = connect_client()
    for attempt in range(10):
        try:
            cc.associate_phone_number_contact_flow(
                PhoneNumberId=phone_number_id,
                InstanceId=instance_id,
                ContactFlowId=flow_id,
            )
            print(f"{OK} Number mapped to contact flow")
            return
        except ClientError as e:
            code = e.response["Error"].get("Code", "")
            if code in ("ResourceNotFoundException", "ResourceInUseException"):
                print(f"     retry {attempt+1}/10 in 5s (got {code})")
                time.sleep(5)
                continue
            raise
    raise RuntimeError("map_number_to_flow failed after retries")


def main() -> int:
    state = load_state()
    lex_state = load_lex_state()
    bot_alias_arn = lex_state["BotAliasArn"]
    lex_bot_id = lex_state["BotId"]
    lex_alias_id = lex_state["BotAliasId"]
    aws_region = lex_state.get("Region", region())
    print(f"=== Atrium Connect provisioning (region={region()}, account={get_account_id()}) ===")
    print(f"     Lex bot: {lex_bot_id} / alias: {lex_alias_id}")

    instance = ensure_instance()
    instance_id = instance["Id"]
    instance_arn = instance["Arn"]
    print(f"{OK} Instance ACTIVE: {instance_arn}")

    associate_lex_bot(instance_id, bot_alias_arn)

    flow_id = upsert_contact_flow(
        instance_id,
        build_flow_content(lex_bot_id, lex_alias_id, aws_region),
    )

    number_info = find_claimed_number(instance_arn)
    if not number_info:
        raise SystemExit(
            "No phone number claimed on the Connect instance. Claim one in the console, "
            "then re-run this script."
        )
    phone_number_id = number_info["PhoneNumberId"]
    print(f"{OK} Phone number: {number_info.get('PhoneNumber')} ({phone_number_id})")
    map_number_to_flow(phone_number_id, instance_id, flow_id)

    state.update({
        "InstanceId": instance_id,
        "InstanceArn": instance_arn,
        "InstanceAlias": INSTANCE_ALIAS,
        "ContactFlowId": flow_id,
        "ContactFlowName": CONTACT_FLOW_NAME,
        "PhoneNumber": number_info.get("PhoneNumber"),
        "PhoneNumberId": phone_number_id,
        "PhoneNumberArn": number_info.get("PhoneNumberArn"),
        "BotAliasArn": bot_alias_arn,
        "Region": region(),
        "AccountId": get_account_id(),
    })
    save_state(state)
    print(f"\n=== Done. State written to {STATE_FILE} ===")
    print(json.dumps(state, indent=2))
    print(f"\n>>> CALL THIS NUMBER: {state.get('PhoneNumber')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
