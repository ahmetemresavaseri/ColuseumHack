"""Atrium Input Agent Lambda — Stage 2 skeleton.

Invoked by the Amazon Connect contact flow via the "Invoke AWS Lambda function" block.

At this stage the function just logs the incoming Connect event and returns a
greeting attribute that the contact flow speaks via Polly. The real Nova Sonic
bidi bridge is wired in Stage 3.

Return-value contract: Connect expects a flat string-keyed dict. Each key
becomes accessible in the contact flow as $.External.<key>.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

PERSONA_NAME = os.environ.get("PERSONA_NAME", "Sarah")
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Sparkle Cleaning")


def lambda_handler(event, context):
    logger.info("CONNECT_EVENT %s", json.dumps(event, default=str))

    details = event.get("Details", {})
    contact_data = details.get("ContactData", {})
    contact_id = contact_data.get("ContactId", "unknown")
    channel = contact_data.get("Channel", "unknown")
    customer_endpoint = contact_data.get("CustomerEndpoint", {}).get("Address", "unknown")

    logger.info(
        "STAGE2_INVOKE contact_id=%s channel=%s customer=%s",
        contact_id, channel, customer_endpoint,
    )

    greeting = (
        f"Hello, this is {PERSONA_NAME} from {COMPANY_NAME}. "
        f"This is the Atrium input agent Lambda speaking, "
        f"stage two of the voice agent is now live. "
        f"In the next stage, you will be talking to me directly through Nova Sonic. "
        f"Goodbye for now."
    )

    return {
        "greeting": greeting,
        "contactId": contact_id,
        "stage": "2",
    }


# Keep the legacy name `handler` as an alias so existing wiring doesn't break.
handler = lambda_handler
