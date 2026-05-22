"""Deterministic pricing helper used by Brain before/after model reasoning.

Supports two price-matrix schemas:

1. Legacy flat schema (glanz-ag) — keys: baseFee, ratePerSquareMeter,
   roomSurcharge, urgentMultiplier. Single urgency tier ("urgent").

2. Rich schema (atrium-demo) — keys: baseFee, pricePerSquareMeter,
   pricePerRoom, pricePerBathroom, minutesPerSquareMeter, minutesPerRoom,
   minutesPerBathroom, minimumPrice. Combined with the Companies row's
   urgencyRules / conditionRules / addons policy bundle (passed in `rules`).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from service_taxonomy import normalize_service

ONE = Decimal("1")
ZERO = Decimal("0")


def _dec(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _norm_key(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def estimate_price(
    slots: dict[str, Any],
    price_matrix: dict[str, Any],
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    service_type = normalize_service(slots.get("what"))
    if service_type is None:
        return {"status": "needs_service_type", "missing": ["what"]}

    row = price_matrix.get(str(service_type), {})
    if not row:
        return {"status": "missing_price_matrix", "serviceType": str(service_type)}

    if "pricePerSquareMeter" in row:
        return _estimate_rich(service_type, slots, row, rules or {})
    return _estimate_legacy(service_type, slots, row)


def _estimate_legacy(service_type, slots, row) -> dict[str, Any]:
    area = _dec(slots.get("area"))
    rooms = _dec(slots.get("rooms"))
    base_fee = _dec(row.get("baseFee"))
    area_rate = _dec(row.get("ratePerSquareMeter"))
    room_surcharge = _dec(row.get("roomSurcharge"))
    multiplier = _dec(row.get("urgentMultiplier"), "1") if slots.get("urgency") == "urgent" else ONE

    subtotal = base_fee + (area * area_rate) + (rooms * room_surcharge)
    total = (subtotal * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "status": "estimated",
        "serviceType": str(service_type),
        "price": float(total),
        "currency": row.get("currency", "CHF"),
        "needsPhotos": str(service_type) in {"CONSTRUCTION_CLEANING", "WINDOW_CLEANING"},
    }


def _estimate_rich(service_type, slots, row, rules) -> dict[str, Any]:
    defaults = rules.get("defaultValues", {}) or {}
    urgency_rules = rules.get("urgencyRules", {}) or {}
    condition_rules = rules.get("conditionRules", {}) or {}
    addon_catalog = rules.get("addons", {}) or {}

    area = _dec(slots.get("area_m2", slots.get("area")))
    rooms = _dec(slots.get("rooms"))
    bathrooms = _dec(slots.get("bathrooms", defaults.get("bathrooms", 0)))

    urgency_key = _norm_key(slots.get("urgency") or defaults.get("urgency") or "STANDARD")
    condition_key = _norm_key(slots.get("condition") or defaults.get("condition") or "NORMAL")
    raw_addons = slots.get("addons")
    if raw_addons is None:
        raw_addons = defaults.get("addons", [])
    addon_keys = [_norm_key(a) for a in raw_addons]

    addon_price = ZERO
    addon_minutes = ZERO
    for key in addon_keys:
        entry = addon_catalog.get(key)
        if not entry:
            continue
        addon_price += _dec(entry.get("price"))
        addon_minutes += _dec(entry.get("minutes"))

    base = (
        _dec(row.get("baseFee"))
        + area * _dec(row.get("pricePerSquareMeter"))
        + rooms * _dec(row.get("pricePerRoom"))
        + bathrooms * _dec(row.get("pricePerBathroom"))
        + addon_price
    )

    urgency = urgency_rules.get(urgency_key, {})
    condition = condition_rules.get(condition_key, {})
    u_mult = _dec(urgency.get("multiplier"), "1")
    u_fee = _dec(urgency.get("fixedFee"))
    c_mult = _dec(condition.get("multiplier"), "1")

    minimum = _dec(row.get("minimumPrice"))
    computed = base * u_mult * c_mult + u_fee
    final = max(computed, minimum).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    minutes = (
        area * _dec(row.get("minutesPerSquareMeter"))
        + rooms * _dec(row.get("minutesPerRoom"))
        + bathrooms * _dec(row.get("minutesPerBathroom"))
        + addon_minutes
    )
    hours = (minutes / Decimal("60")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    low = (final * Decimal("0.9")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    high = (final * Decimal("1.1")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    needs_photos = (
        condition_key in {"HEAVY", "UNKNOWN"}
        or str(service_type) == "CONSTRUCTION_CLEANING"
        or area > Decimal("150")
    )

    return {
        "status": "estimated",
        "serviceType": str(service_type),
        "price": float(final),
        "priceRange": {"low": float(low), "high": float(high)},
        "currency": row.get("currency", "CHF"),
        "estimatedMinutes": int(minutes),
        "estimatedHours": float(hours),
        "needsPhotos": needs_photos,
        "urgency": urgency_key,
        "condition": condition_key,
        "addons": addon_keys,
    }
