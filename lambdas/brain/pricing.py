"""Deterministic pricing helper used by Brain before/after model reasoning.

Formula:
    Price = [Base Fee + (Area × Rate) + (Rooms × Surcharge)] × Urgency Multiplier

- Base Fee, Rate (per m²), Surcharge (per room) come from the company's
  PriceMatrix row for the chosen service type.
- Urgency Multiplier is mapped from the caller's "urgency" slot:
      low    → 1.00
      medium → priceMatrix.mediumMultiplier (defaults to 1.10)
      high   → priceMatrix.urgentMultiplier (defaults to 1.25)
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from service_taxonomy import normalize_service

_DEFAULT_MEDIUM = Decimal("1.10")
_DEFAULT_URGENT = Decimal("1.25")


def _urgency_multiplier(urgency_raw: Any, row: dict[str, Any]) -> Decimal:
    """Map a caller urgency value to a numeric multiplier."""
    urgency = str(urgency_raw or "").strip().lower()
    if urgency in ("high", "urgent", "asap", "3"):
        return Decimal(str(row.get("urgentMultiplier", _DEFAULT_URGENT)))
    if urgency in ("medium", "med", "normal", "2"):
        return Decimal(str(row.get("mediumMultiplier", _DEFAULT_MEDIUM)))
    # low, calm, "1", empty, unknown → no surcharge
    return Decimal("1")


def estimate_price(slots: dict[str, Any], price_matrix: dict[str, Any]) -> dict[str, Any]:
    service_type = normalize_service(slots.get("what"))
    if service_type is None:
        return {"status": "needs_service_type", "missing": ["what"]}

    row = price_matrix.get(str(service_type), {})
    if not row:
        return {"status": "missing_price_matrix", "serviceType": str(service_type)}

    # Inputs from the call slots.
    area = Decimal(str(slots.get("area") or 0))
    rooms = Decimal(str(slots.get("rooms") or 0))

    # Tenant-specific pricing parameters.
    base_fee = Decimal(str(row.get("baseFee", 0)))
    area_rate = Decimal(str(row.get("ratePerSquareMeter", 0)))
    room_surcharge = Decimal(str(row.get("roomSurcharge", 0)))
    multiplier = _urgency_multiplier(slots.get("urgency"), row)

    # Price = [Base Fee + (Area × Rate) + (Rooms × Surcharge)] × Urgency Multiplier
    subtotal = base_fee + (area * area_rate) + (rooms * room_surcharge)
    total = (subtotal * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "status": "estimated",
        "serviceType": str(service_type),
        "price": float(total),
        "currency": row.get("currency", "CHF"),
        "needsPhotos": str(service_type) in {"CONSTRUCTION_CLEANING", "WINDOW_CLEANING"},
        "breakdown": {
            "baseFee": float(base_fee),
            "areaComponent": float(area * area_rate),
            "roomsComponent": float(rooms * room_surcharge),
            "urgencyMultiplier": float(multiplier),
            "subtotal": float(subtotal),
        },
    }
