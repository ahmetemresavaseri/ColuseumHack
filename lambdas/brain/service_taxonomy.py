"""Canonical cleaning service types."""
from __future__ import annotations

from enum import StrEnum


class ServiceType(StrEnum):
    MOVE_OUT_CLEANING = "MOVE_OUT_CLEANING"
    OFFICE_CLEANING = "OFFICE_CLEANING"
    CONSTRUCTION_CLEANING = "CONSTRUCTION_CLEANING"
    WINDOW_CLEANING = "WINDOW_CLEANING"
    FACILITY_MAINTENANCE = "FACILITY_MAINTENANCE"


ALIASES = {
    "move out": ServiceType.MOVE_OUT_CLEANING,
    "end of tenancy": ServiceType.MOVE_OUT_CLEANING,
    "office": ServiceType.OFFICE_CLEANING,
    "construction": ServiceType.CONSTRUCTION_CLEANING,
    "window": ServiceType.WINDOW_CLEANING,
    "facility": ServiceType.FACILITY_MAINTENANCE,
}


def normalize_service(value: str | None) -> ServiceType | None:
    if not value:
        return None
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    if normalized in ServiceType.__members__:
        return ServiceType[normalized]
    lowered = value.strip().lower()
    for needle, service_type in ALIASES.items():
        if needle in lowered:
            return service_type
    return None
