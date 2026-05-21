"""DEPRECATED — Amazon Connect is NOT on the hackathon allow-list.

This script provisioned an Amazon Connect instance + claimed PSTN number +
contact flow. As of 2026-05-22 the Atrium hackathon build uses a Browser-WebRTC
front door (see plan section "Architecture") instead of PSTN, because Amazon
Connect is not on the event's allowed-services list.

Kept in the repo for the post-hackathon PSTN drop-in roadmap item. Do NOT run
during the hackathon — `aws_helpers.connect_client()` was also removed, so
this file will fail on import; that is intentional.

To re-enable post-hackathon: restore `connect_client()` in aws_helpers.py and
re-add `connect:*` to the Input Agent IAM role in deploy_lambda.py.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

raise SystemExit(
    "provision_connect.py is DEPRECATED for the hackathon — Amazon Connect is "
    "not on the allow-list. Use the WebRTC front door (see infrastructure/cdk_app.py "
    "and the 'Architecture' section of the plan)."
)

sys.path.insert(0, str(Path(__file__).parent))

import boto3
from botocore.exceptions import ClientError

from aws_helpers import connect_client, get_account_id, region  # noqa: F401 — kept for the post-hackathon restore

INSTANCE_ALIAS = "atrium-demo"
CONTACT_FLOW_NAME = "atrium-inbound"
STATE_FILE = Path(__file__).parent / ".connect_state.json"

OK = "[OK]"
WAIT = "[..]"
FAIL = "[FAIL]"


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def find_instance_by_alias(alias: str) -> dict | None:
    cc = connect_client()
    paginator = cc.get_paginator("list_instances")
    for page in paginator.paginate():
        for inst in page["InstanceSummaryList"]:
            if inst.get("InstanceAlias") == alias:
                return inst
    return None


def create_instance(alias: str) -> dict:
    print(f"{WAIT} Creating Connect instance '{alias}' (this takes 2-5 min) ...")
    cc = connect_client()
    try:
        resp = cc.create_instance(
            IdentityManagementType="CONNECT_MANAGED",
            InstanceAlias=alias,
            InboundCallsEnabled=True,
            OutboundCallsEnabled=False,
        )
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code == "AccessDeniedException":
            print(f"{FAIL} Workshop role lacks connect:CreateInstance permission.")
            print("       Activate Fallback B (Browser-WebRTC pivot) — talk to user.")
            raise SystemExit(2)
        raise
    instance_id = resp["Id"]
    print(f"     instance id = {instance_id}, polling status ...")
    # Poll for ACTIVE — typical ~2 min
    for attempt in range(60):
        time.sleep(10)
        try:
            d = cc.describe_instance(InstanceId=instance_id)["Instance"]
            status = d.get("InstanceStatus")
            print(f"     [{attempt*10:3d}s] status={status}")
            if status == "ACTIVE":
                return d
            if status == "CREATION_FAILED":
                print(f"{FAIL} Instance creation failed: {d.get('StatusReason')}")
                raise SystemExit(2)
        except ClientError as e:
            if e.response["Error"].get("Code") == "ResourceNotFoundException":
                continue
            raise
    print(f"{FAIL} Instance didn't reach ACTIVE within 10 min")
    raise SystemExit(2)


def ensure_instance() -> dict:
    found = find_instance_by_alias(INSTANCE_ALIAS)
    if found:
        print(f"{OK} Connect instance already exists: {found['Id']} (status={found.get('InstanceStatus')})")
        # If existing but not ACTIVE, wait
        if found.get("InstanceStatus") != "ACTIVE":
            cc = connect_client()
            for _ in range(30):
                time.sleep(5)
                d = cc.describe_instance(InstanceId=found["Id"])["Instance"]
                print(f"     polling existing instance... status={d.get('InstanceStatus')}")
                if d.get("InstanceStatus") == "ACTIVE":
                    return d
            print(f"{FAIL} Existing instance not ACTIVE")
            raise SystemExit(2)
        return found
    return create_instance(INSTANCE_ALIAS)


def get_default_flow_id(instance_id: str) -> str:
    """Find the default 'Sample inbound flow' as a placeholder for now."""
    cc = connect_client()
    paginator = cc.get_paginator("list_contact_flows")
    for page in paginator.paginate(InstanceId=instance_id, ContactFlowTypes=["CONTACT_FLOW"]):
        for f in page["ContactFlowSummaryList"]:
            if f["Name"] == "Sample inbound flow (first contact experience)":
                return f["Id"]
            if f["Name"] == "Default customer queue":
                return f["Id"]
    # If no default found, return the first one
    for page in paginator.paginate(InstanceId=instance_id, ContactFlowTypes=["CONTACT_FLOW"]):
        for f in page["ContactFlowSummaryList"]:
            return f["Id"]
    raise RuntimeError("No default flow found")


GREETING_FLOW_CONTENT = {
    "Version": "2019-10-30",
    "StartAction": "greeting",
    "Metadata": {
        "entryPointPosition": {"x": 40, "y": 40},
        "ActionMetadata": {
            "greeting": {"position": {"x": 240, "y": 40}},
            "disconnect": {"position": {"x": 440, "y": 40}},
        },
    },
    "Actions": [
        {
            "Identifier": "greeting",
            "Type": "MessageParticipant",
            "Parameters": {
                "Text": "Hello from Atrium. This is a placeholder greeting. The voice agent is being wired up.",
            },
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


def ensure_contact_flow(instance_id: str) -> str:
    cc = connect_client()
    # Search existing
    paginator = cc.get_paginator("list_contact_flows")
    for page in paginator.paginate(InstanceId=instance_id, ContactFlowTypes=["CONTACT_FLOW"]):
        for f in page["ContactFlowSummaryList"]:
            if f["Name"] == CONTACT_FLOW_NAME:
                print(f"{OK} Contact flow '{CONTACT_FLOW_NAME}' already exists: {f['Id']}")
                return f["Id"]
    print(f"{WAIT} Creating contact flow '{CONTACT_FLOW_NAME}' ...")
    resp = cc.create_contact_flow(
        InstanceId=instance_id,
        Name=CONTACT_FLOW_NAME,
        Type="CONTACT_FLOW",
        Description="Atrium inbound greeting; replaced by Lambda invocation in Stage 2",
        Content=json.dumps(GREETING_FLOW_CONTENT),
    )
    flow_id = resp["ContactFlowId"]
    print(f"{OK} Contact flow created: {flow_id}")
    return flow_id


def find_claimed_number(instance_arn: str) -> dict | None:
    cc = connect_client()
    paginator = cc.get_paginator("list_phone_numbers_v2")
    for page in paginator.paginate(TargetArn=instance_arn):
        for n in page["ListPhoneNumbersSummaryList"]:
            return n  # Return first one if any
    return None


def claim_phone_number(instance_arn: str) -> dict:
    cc = connect_client()
    print(f"{WAIT} Searching for available US DID numbers ...")
    try:
        avail = cc.search_available_phone_numbers(
            TargetArn=instance_arn,
            PhoneNumberCountryCode="US",
            PhoneNumberType="DID",
            MaxResults=5,
        )
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code == "AccessDeniedException":
            print(f"{FAIL} Workshop role lacks connect:SearchAvailablePhoneNumbers")
            raise SystemExit(3)
        raise
    candidates = avail.get("AvailableNumbersList", [])
    if not candidates:
        print(f"{FAIL} No DID numbers available — trying TOLL_FREE ...")
        avail = cc.search_available_phone_numbers(
            TargetArn=instance_arn,
            PhoneNumberCountryCode="US",
            PhoneNumberType="TOLL_FREE",
            MaxResults=5,
        )
        candidates = avail.get("AvailableNumbersList", [])
        if not candidates:
            print(f"{FAIL} No US numbers available at all in this account")
            raise SystemExit(3)
    chosen = candidates[0]
    print(f"     Claiming {chosen['PhoneNumber']} ({chosen['PhoneNumberType']}) ...")
    try:
        resp = cc.claim_phone_number(
            TargetArn=instance_arn,
            PhoneNumber=chosen["PhoneNumber"],
            PhoneNumberDescription="Atrium demo line",
        )
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code == "AccessDeniedException":
            print(f"{FAIL} Workshop role lacks connect:ClaimPhoneNumber — Fallback B needed")
            raise SystemExit(3)
        raise
    return {
        "PhoneNumberId": resp["PhoneNumberId"],
        "PhoneNumberArn": resp["PhoneNumberArn"],
        "PhoneNumber": chosen["PhoneNumber"],
        "PhoneNumberType": chosen["PhoneNumberType"],
    }


def map_number_to_flow(phone_number_id: str, flow_id: str, instance_id: str) -> None:
    cc = connect_client()
    print(f"{WAIT} Mapping number {phone_number_id} -> flow {flow_id} ...")
    # Claim can take a few seconds to propagate — retry up to ~30s
    last_err: Exception | None = None
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
                last_err = e
                print(f"     retry {attempt+1}/10 in 5s (got {code})")
                time.sleep(5)
                continue
            raise
    raise RuntimeError(f"map_number_to_flow failed after retries: {last_err}")


def main() -> int:
    state = load_state()
    print(f"=== Atrium Connect provisioning (region={region()}, account={get_account_id()}) ===")

    instance = ensure_instance()
    instance_id = instance["Id"]
    instance_arn = instance["Arn"]
    print(f"{OK} Instance ACTIVE: {instance_arn}")

    flow_id = ensure_contact_flow(instance_id)

    number_info = find_claimed_number(instance_arn)
    if number_info:
        print(f"{OK} Phone number already claimed: {number_info.get('PhoneNumber')}")
        number_info = {
            "PhoneNumberId": number_info["PhoneNumberId"],
            "PhoneNumberArn": number_info["PhoneNumberArn"],
            "PhoneNumber": number_info.get("PhoneNumber"),
            "PhoneNumberType": number_info.get("PhoneNumberType"),
        }
    else:
        number_info = claim_phone_number(instance_arn)
        print(f"{OK} Claimed: {number_info['PhoneNumber']} ({number_info['PhoneNumberType']})")

    map_number_to_flow(number_info["PhoneNumberId"], flow_id, instance_id)

    state = {
        "InstanceId": instance_id,
        "InstanceArn": instance_arn,
        "InstanceAlias": INSTANCE_ALIAS,
        "ContactFlowId": flow_id,
        "ContactFlowName": CONTACT_FLOW_NAME,
        **number_info,
        "Region": region(),
        "AccountId": get_account_id(),
    }
    save_state(state)
    print(f"\n=== Done. State written to {STATE_FILE} ===")
    print(json.dumps(state, indent=2))
    print(f"\n>>> CALL THIS NUMBER: {state.get('PhoneNumber')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
