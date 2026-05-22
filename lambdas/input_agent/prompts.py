"""Prompt fragments for the live voice Input Agent."""
from __future__ import annotations

SYSTEM_PROMPT = """You are the Atrium Input Agent.
Capture slots, answer grounded FAQ questions from retrieved context, and call
tools instead of inventing prices or company policy.
"""

REQUIRED_SLOTS = ("when", "what", "area", "rooms", "urgency", "location")


def persona_prompt(company: dict) -> str:
    name = company.get("name", "the cleaning company")
    locale = company.get("locale", "de-CH")
    persona = company.get("voicePersonaPrompt", "")
    return f"{SYSTEM_PROMPT}\nCompany: {name}\nLocale: {locale}\nPersona: {persona}"
