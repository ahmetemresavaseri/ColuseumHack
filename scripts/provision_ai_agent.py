"""Stage 3 — provision Amazon Q in Connect assistant + AI Agent + AI Prompt.

This sets up the *reasoning* side of the agent: the AI Agent that decides
what to say, which tools to call, and which slots to extract.

The audio side (Nova Sonic Speech-to-Speech) is configured at the Lex Bot +
Contact Flow level in the next script (provision_lex_bot.py).

Idempotent. Writes results to scripts/.connect_state.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import boto3
from botocore.exceptions import ClientError

from aws_helpers import get_account_id, region

STATE_FILE = Path(__file__).parent / ".connect_state.json"
ASSISTANT_NAME = "atrium-assistant"
AI_PROMPT_NAME = "atrium-sarah-prompt"
AI_AGENT_NAME = "atrium-sarah-agent"

# Sarah persona + 6-slot voice agent system prompt for Q in Connect
# This is the SELF_SERVICE_ANSWER_GENERATION prompt — what the agent thinks/says.
SARAH_PROMPT_TEXT = """\
You are Sarah, a warm and professional receptionist at Sparkle Cleaning, a US-based cleaning company. \
You speak fluent American English with a friendly tone. Your job is to take incoming booking calls \
from potential customers and gather their cleaning request.

When the call starts, greet the caller warmly: "Hi, this is Sarah from Sparkle Cleaning, how can I help you today?"

Then gather these six slots in a natural conversational order (don't read them as a checklist):
1. WHEN — preferred date and time of the cleaning
2. WHAT — type of service. Always map the caller's words to exactly one of:
   MOVE_OUT_CLEANING, OFFICE_CLEANING, CONSTRUCTION_CLEANING, WINDOW_CLEANING, FACILITY_MAINTENANCE
3. AREA — size in square feet (sqft). Always confirm the unit.
4. ROOMS — number of rooms / bedrooms
5. URGENCY — low, medium, or high
6. EMAIL — caller's email address for the booking confirmation

Rules:
- Whenever the caller gives you a piece of information, call the `save_slot` tool immediately.
- As soon as you know both WHAT and AREA, call `compute_price` to get a price estimate. \
Tell the caller the estimate naturally ("Based on that, you're looking at about $540 with our Team 3, around 4.5 hours").
- If the caller asks a question (pricing per sqft, what's included, postal codes, cancellation policy), \
call `kb_lookup` with their question and base your answer ONLY on what the tool returns. \
If the tool returns nothing relevant, honestly say "I don't have that information" — DO NOT make things up.
- Once all six slots are collected and you've shared the price, summarize ("So that's a MOVE_OUT_CLEANING on Friday for 1100 sqft, 3 rooms, medium urgency, emailed to test@example.com, estimate around $540"). \
Then thank the caller and call the `end_call` tool with a brief reason.

Keep responses short (1-2 sentences). Speak naturally — this is a phone call, not an email."""

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
    # Search existing
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
        description="Atrium voice agent assistant (Sarah, Sparkle Cleaning, en-US)",
    )
    assistant_id = r["assistant"]["assistantId"]
    print(f"{OK} Assistant created: {assistant_id}")
    return assistant_id


def ensure_ai_prompt(assistant_id: str) -> str:
    qc = qconnect()
    paginator = qc.get_paginator("list_ai_prompts")
    for page in paginator.paginate(assistantId=assistant_id):
        for p in page.get("aiPromptSummaries", []):
            if p["name"] == AI_PROMPT_NAME:
                print(f"{OK} AI Prompt exists: {p['aiPromptId']}")
                return p["aiPromptId"]
    print(f"{WAIT} Creating AI Prompt '{AI_PROMPT_NAME}' (Sarah persona, 6-slot form) ...")
    r = qc.create_ai_prompt(
        assistantId=assistant_id,
        name=AI_PROMPT_NAME,
        type="SELF_SERVICE_ANSWER_GENERATION",
        description="Sarah persona + 6-slot intake prompt",
        modelId="anthropic.claude-sonnet-4-6",
        apiFormat="ANTHROPIC_CLAUDE_MESSAGES",
        templateType="TEXT",
        templateConfiguration={
            "textFullAIPromptEditTemplateConfiguration": {
                "text": SARAH_PROMPT_TEXT,
            },
        },
        visibilityStatus="PUBLISHED",
    )
    prompt_id = r["aiPrompt"]["aiPromptId"]
    print(f"{OK} AI Prompt created: {prompt_id}")
    return prompt_id


def ensure_ai_agent(assistant_id: str, prompt_id: str) -> str:
    qc = qconnect()
    paginator = qc.get_paginator("list_ai_agents")
    for page in paginator.paginate(assistantId=assistant_id):
        for a in page.get("aiAgentSummaries", []):
            if a["name"] == AI_AGENT_NAME:
                print(f"{OK} AI Agent exists: {a['aiAgentId']}")
                return a["aiAgentId"]
    print(f"{WAIT} Creating AI Agent '{AI_AGENT_NAME}' (type=SELF_SERVICE) ...")
    r = qc.create_ai_agent(
        assistantId=assistant_id,
        name=AI_AGENT_NAME,
        type="SELF_SERVICE",
        description="Sarah AI agent for inbound voice booking calls",
        configuration={
            "selfServiceAIAgentConfiguration": {
                "selfServiceAnswerGenerationAIPromptId": prompt_id,
            },
        },
        visibilityStatus="PUBLISHED",
    )
    agent_id = r["aiAgent"]["aiAgentId"]
    print(f"{OK} AI Agent created: {agent_id}")
    return agent_id


def set_default_ai_agent(assistant_id: str, agent_id: str) -> None:
    qc = qconnect()
    print(f"{WAIT} Setting default AI Agent for assistant ...")
    qc.update_assistant_ai_agent(
        assistantId=assistant_id,
        aiAgentType="SELF_SERVICE",
        configuration={"aiAgentId": agent_id},
    )
    print(f"{OK} Default AI Agent set to {agent_id}")


def main() -> int:
    state = load_state()
    print(f"=== Q in Connect AI Agent setup (account={get_account_id()}, region={region()}) ===")

    assistant_id = ensure_assistant()
    prompt_id = ensure_ai_prompt(assistant_id)
    agent_id = ensure_ai_agent(assistant_id, prompt_id)
    set_default_ai_agent(assistant_id, agent_id)

    state.update({
        "AssistantId": assistant_id,
        "AiPromptId": prompt_id,
        "AiAgentId": agent_id,
    })
    save_state(state)
    print(f"\n=== Done. AI Agent ready. Next: provision_lex_bot.py ===")
    print(json.dumps({k: v for k, v in state.items() if "Id" in k}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
