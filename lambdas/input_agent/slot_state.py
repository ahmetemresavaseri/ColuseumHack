"""Slot state helpers shared by the Input Agent and local simulations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REQUIRED_SLOTS = ("when", "where", "what", "area", "rooms", "urgency", "email")


@dataclass
class SlotState:
    when: str | None = None
    where: str | None = None
    what: str | None = None
    area: float | None = None
    rooms: int | None = None
    urgency: str | None = None
    email: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "SlotState":
        value = value or {}
        known = {key: value.get(key) for key in REQUIRED_SLOTS}
        raw = {key: item for key, item in value.items() if key not in REQUIRED_SLOTS}
        return cls(**known, raw=raw)

    def update(self, slot: str, value: Any) -> None:
        if slot not in REQUIRED_SLOTS:
            self.raw[slot] = value
            return
        setattr(self, slot, value)

    def missing(self) -> list[str]:
        return [slot for slot in REQUIRED_SLOTS if getattr(self, slot) in (None, "")]

    def ready_for_price(self) -> bool:
        return not self.missing()

    def as_dict(self) -> dict[str, Any]:
        result = {slot: getattr(self, slot) for slot in REQUIRED_SLOTS}
        result.update(self.raw)
        return result
