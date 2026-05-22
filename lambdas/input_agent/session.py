"""Per-call session state for the Input Agent.

A WebSocket-style Lambda handler is invoked per route (`$connect`, message,
`$disconnect`) and does not have memory between invocations. Production
deployments persist this state in DynamoDB `Connections`; Phase 1 keeps an
in-process registry so the deterministic text-turn path and local simulators
can run a coherent session end-to-end without a real table.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from slot_state import SlotState


@dataclass
class CallSession:
    call_id: str
    booking_id: str
    company_id: str
    connection_id: str | None = None
    company_name: str = ""
    locale: str = ""
    caller: str = ""
    turn_seq: int = 0
    slots: SlotState = field(default_factory=SlotState)
    started: bool = False
    ended: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def next_seq(self) -> int:
        self.turn_seq += 1
        return self.turn_seq


_SESSIONS: dict[str, CallSession] = {}


def new_session(
    company_id: str,
    *,
    connection_id: str | None = None,
    company_name: str | None = None,
    locale: str | None = None,
    caller: str | None = None,
    call_id: str | None = None,
) -> CallSession:
    call_id = call_id or f"call-{uuid.uuid4().hex[:12]}"
    session = CallSession(
        call_id=call_id,
        booking_id=f"booking-{uuid.uuid4().hex[:12]}",
        company_id=company_id,
        connection_id=connection_id,
        company_name=company_name or os.environ.get("COMPANY_NAME", "Atrium Demo"),
        locale=locale or os.environ.get("DEFAULT_LOCALE", "en-US"),
        caller=caller or "",
    )
    _SESSIONS[call_id] = session
    if connection_id:
        _SESSIONS[f"conn:{connection_id}"] = session
    return session


def get_session(call_id: str | None = None, connection_id: str | None = None) -> CallSession | None:
    if call_id and call_id in _SESSIONS:
        return _SESSIONS[call_id]
    if connection_id and f"conn:{connection_id}" in _SESSIONS:
        return _SESSIONS[f"conn:{connection_id}"]
    return None


def drop_session(session: CallSession) -> None:
    _SESSIONS.pop(session.call_id, None)
    if session.connection_id:
        _SESSIONS.pop(f"conn:{session.connection_id}", None)


def reset_all() -> None:
    """Test helper — wipe the in-process registry."""
    _SESSIONS.clear()
