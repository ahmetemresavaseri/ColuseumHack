"""Deterministic pricing helper used by Brain before/after model reasoning."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from service_taxonomy import normalize_service


def estimate_price(slots: dict[str, Any], price_matrix: dict[str, Any]) -> dict[str, Any]:
    service_type = normalize_service(slots.get("what"))
    if service_type is None:
        return {"status": "needs_service_type", "missing": ["what"]}

    row = price_matrix.get(str(service_type), {})
    if not row:
        return {"status": "missing_price_matrix", "serviceType": str(service_type)}

    area = Decimal(str(slots.get("area") or 0))
    rooms = Decimal(str(slots.get("rooms") or 0))
    base_fee = Decimal(str(row.get("baseFee", 0)))
    area_rate = Decimal(str(row.get("ratePerSquareMeter", 0)))
    room_surcharge = Decimal(str(row.get("roomSurcharge", 0)))
    multiplier = Decimal(str(row.get("urgentMultiplier", 1))) if slots.get("urgency") == "urgent" else Decimal("1")

    subtotal = base_fee + (area * area_rate) + (rooms * room_surcharge)
    total = (subtotal * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "status": "estimated",
        "serviceType": str(service_type),
        "price": float(total),
        "currency": row.get("currency", "CHF"),
        "needsPhotos": str(service_type) in {"CONSTRUCTION_CLEANING", "WINDOW_CLEANING"},
    }
