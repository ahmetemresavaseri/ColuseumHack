"""Pricing breakdown from an allocation result + inputs + company config."""
from __future__ import annotations

from typing import Any


def _round2(x: float) -> float:
    return round(float(x) + 1e-9, 2)


def compute(allocation: dict[str, Any], inputs: dict[str, Any],
            company: dict[str, Any]) -> dict[str, Any]:
    pf = company["pricing_formula"]
    cat = allocation["category"]
    service_cfg = company["service_types"][cat]

    people = int(allocation["personnel"]["count"])
    wall_minutes = int(allocation["wall_clock_minutes"])
    area_m2 = float(inputs["area_m2"])
    addons_selected = list(inputs.get("addons", []))

    labor = people * (wall_minutes / 60.0) * float(pf["labor_per_person_per_hour"])

    vehicle_id = allocation["vehicle"]["id"]
    vehicle_base = float(company["vehicles"][vehicle_id]["cost_per_job"])
    distance_km = float(allocation["crew"].get("distanceKm") or 0.0)
    vehicle_cost = vehicle_base + 2 * distance_km * float(pf["travel_cost_per_km"])

    equipment_cost = sum(
        float(company["equipment"][e["id"]]["cost_per_use"]) for e in allocation["equipment"]
    )

    materials_cost = area_m2 * float(pf["materials_per_m2"])

    addons_cost = sum(
        float(company.get("addons", {}).get(a, {}).get("price", 0)) for a in addons_selected
    )

    subtotal = labor + vehicle_cost + equipment_cost + materials_cost + addons_cost

    urgency_key = inputs.get("urgency") or company["default_values"]["urgency"]
    condition_key = inputs.get("condition") or company["default_values"]["condition"]
    u = company["urgency_rules"].get(urgency_key, {"multiplier": 1.0, "fixed_fee": 0})
    c = company["condition_rules"].get(condition_key, {"multiplier": 1.0})

    adjusted = subtotal * float(u["multiplier"]) * float(c["multiplier"]) + float(u.get("fixed_fee", 0))
    minimum = float(service_cfg.get("minimum_price", 0))
    total = max(adjusted, minimum)

    currency = company.get("currency", "CHF")

    return {
        "currency": currency,
        "labor": _round2(labor),
        "vehicle": _round2(vehicle_cost),
        "equipment": _round2(equipment_cost),
        "materials": _round2(materials_cost),
        "addons": _round2(addons_cost),
        "subtotal": _round2(subtotal),
        "urgency": {"key": urgency_key, "multiplier": u["multiplier"], "fixed_fee": u.get("fixed_fee", 0)},
        "condition": {"key": condition_key, "multiplier": c["multiplier"]},
        "minimum_price": _round2(minimum),
        "total": _round2(total),
        "range_low": _round2(total * 0.9),
        "range_high": _round2(total * 1.1),
        "breakdown_formula": "max(subtotal * urgency_mult * condition_mult + urgency_fee, minimum_price)",
    }
