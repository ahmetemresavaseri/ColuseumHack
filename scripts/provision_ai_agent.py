"""DEPRECATED — Q in Connect is NOT on the hackathon allow-list.

This script provisioned a Q-in-Connect (`qconnect`) assistant + AI Agent
(ORCHESTRATION) for the Connect contact flow. As of 2026-05-22 the Atrium
hackathon build uses **direct Bedrock Claude Sonnet 4.6 invocations** from the
Input Agent Lambda (see lambdas/input_agent/handler.py) instead of routing
through Q-in-Connect — `qconnect` is not on the event's allowed-services list.

The persona / 6-slot system prompt lives in `prompts/sarah_orchestration.yaml`
and is loaded directly by the Input Agent Lambda at start-up.

Kept in the repo for the post-hackathon Connect drop-in roadmap item. Do NOT
run during the hackathon.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

raise SystemExit(
    "provision_ai_agent.py is DEPRECATED for the hackathon — Q in Connect "
    "(qconnect) is not on the allow-list. The persona prompt is loaded "
    "directly by lambdas/input_agent/handler.py from prompts/sarah_orchestration.yaml."
)

sys.path.insert(0, str(Path(__file__).parent))

import boto3
from botocore.exceptions import ClientError

from aws_helpers import get_account_id, region

STATE_FILE = Path(__file__).parent / ".connect_state.json"
PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "sarah_orchestration.yaml"

ASSISTANT_NAME = "atrium-assistant"
AI_PROMPT_NAME = "atrium-sarah-orchestration"
AI_AGENT_NAME = "atrium-sarah-agent"

ORCHESTRATION_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

OK = "[OK]"
WAIT = "[..]"
FAIL = "[FAIL]"


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def qconnect():
    return boto3.client("qconnect", region_name=region())


def ensure_assistant() -> str:
    qc = qconnect()
    paginator = qc.get_paginator("list_assistants")
    for page in paginator.paginate():
        for a in page.get("assistantSummaries", []):
            if a["name"] == ASSISTANT_NAME:
                print(f"{OK} Assistant exists: {a['assistantId']}")
                return a["assistantId"]
    print(f"{WAIT} Creating Q in Connect assistant '{ASSISTANT_NAME}' ...")
    r = qc.create_assistant(
        name=ASSISTANT_NAME,
        type="AGENT",
        description="Atrium voice agent (Sarah, Sparkle Cleaning, en-US)",
    )
    aid = r["assistant"]["assistantId"]
    print(f"{OK} Assistant created: {aid}")
    return aid


def ensure_ai_prompt(assistant_id: str) -> str:
    qc = qconnect()
    paginator = qc.get_paginator("list_ai_prompts")
    for page in paginator.paginate(assistantId=assistant_id):
        for p in page.get("aiPromptSummaries", []):
            if p["name"] == AI_PROMPT_NAME:
                print(f"{OK} AI Prompt exists: {p['aiPromptId']}")
                return p["aiPromptId"]
    yaml_text = PROMPT_FILE.read_text(encoding="utf-8")
    print(f"{WAIT} Creating AI Prompt '{AI_PROMPT_NAME}' (ORCHESTRATION, {len(yaml_text)} chars) ...")
    r = qc.create_ai_prompt(
        assistantId=assistant_id,
        name=AI_PROMPT_NAME,
        type="ORCHESTRATION",
        description="Sarah persona + 6-slot voice agent prompt",
        modelId=ORCHESTRATION_MODEL_ID,
        apiFormat="MESSAGES",
        templateType="TEXT",
        templateConfiguration={
            "textFullAIPromptEditTemplateConfiguration": {"text": yaml_text},
        },
        visibilityStatus="PUBLISHED",
    )
    pid = r["aiPrompt"]["aiPromptId"]
    print(f"{OK} AI Prompt created: {pid}")
    return pid


def ensure_ai_agent(assistant_id: str, prompt_id: str, connect_instance_arn: str) -> str:
    qc = qconnect()
    paginator = qc.get_paginator("list_ai_agents")
    for page in paginator.paginate(assistantId=assistant_id):
        for a in page.get("aiAgentSummaries", []):
            if a["name"] == AI_AGENT_NAME:
                print(f"{OK} AI Agent exists: {a['aiAgentId']}")
                return a["aiAgentId"]
    print(f"{WAIT} Creating AI Agent '{AI_AGENT_NAME}' (type=ORCHESTRATION, no tools yet) ...")
    r = qc.create_ai_agent(
        assistantId=assistant_id,
        name=AI_AGENT_NAME,
        type="ORCHESTRATION",
        description="Sarah AI orchestration agent for inbound voice booking calls",
        configuration={
            "orchestrationAIAgentConfiguration": {
                "orchestrationAIPromptId": prompt_id,
                "connectInstanceArn": connect_instance_arn,
                "locale": "en_US",
                "toolConfigurations": [],  # tools come in Stage 5
            },
        },
        visibilityStatus="PUBLISHED",
    )
    agent_id = r["aiAgent"]["aiAgentId"]
    print(f"{OK} AI Agent created: {agent_id}")
    return agent_id


def set_default_ai_agent(assistant_id: str, agent_id: str) -> None:
    qc = qconnect()
    print(f"{WAIT} Setting default ORCHESTRATION AI Agent ...")
    qc.update_assistant_ai_agent(
        assistantId=assistant_id,
        aiAgentType="ORCHESTRATION",
        configuration={"aiAgentId": agent_id},
    )
    print(f"{OK} Default ORCHESTRATION AI Agent set to {agent_id}")


def main() -> int:
    state = load_state()
    instance_arn = state["InstanceArn"]
    print(f"=== Q in Connect AI Agent setup (account={get_account_id()}, region={region()}) ===")

    assistant_id = ensure_assistant()
    prompt_id = ensure_ai_prompt(assistant_id)
    agent_id = ensure_ai_agent(assistant_id, prompt_id, instance_arn)
    set_default_ai_agent(assistant_id, agent_id)

    state.update({
        "AssistantId": assistant_id,
        "AiPromptId": prompt_id,
        "AiAgentId": agent_id,
    })
    save_state(state)
    print(f"\n=== Done. AI Agent ready. Next: provision_lex_bot.py ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
