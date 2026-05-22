"""Deterministic allocation: 5 inputs -> personnel, vehicle, equipment, crew.

Inputs schema (dict):
    when:     str ISO date or natural phrase (parsed by resolve.py, not here)
    where:    str address / postcode / city  (e.g. "8001 Zurich")
    area_m2:  number
    rooms:    int
    category: str service_type key (e.g. "move_out_cleaning")
    # optional, with defaults if omitted:
    bathrooms: int (default 1)
    urgency:   str (default "standard")
    condition: str (default "normal")
    addons:    list[str] (default [])
"""
from __future__ import annotations

import math
import re
from typing import Any


def _extract_postcode(where: str | None) -> str | None:
    if not where:
        return None
    match = re.search(r"\b(\d{4})\b", str(where))
    return match.group(1) if match else None


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _personnel_count(area_m2: float, rooms: int, category: str, company: dict) -> tuple[int, str]:
    rules = company["personnel_rules"]
    override = rules.get("category_overrides", {}).get(category, {})

    if "override_people" in override:
        base = int(override["override_people"])
        rationale = f"category override → fixed {base} people ({override.get('reason', '')})"
    elif "per_100m2" in override:
        per = float(override["per_100m2"])
        floor = int(override.get("min", rules["default_min"]))
        base = max(floor, math.ceil(area_m2 / 100 * per))
        rationale = f"office rule: ceil({area_m2}/100 × {per}) = {base} (min {floor})"
    else:
        tier = next(t for t in rules["tiers_by_m2"] if area_m2 <= t["max_m2"])
        base = int(tier["people"])
        rationale = f"{area_m2}m² → tier ≤{tier['max_m2']}m² → {base} people"
        add = int(override.get("add_people", 0))
        if add:
            base += add
            rationale += f" + {add} ({override.get('reason', 'category override')})"

    bonus_rule = rules.get("rooms_bonus", {})
    threshold = int(bonus_rule.get("threshold", 999))
    if rooms >= threshold:
        add = int(bonus_rule.get("add_people", 0))
        base += add
        rationale += f" + {add} (rooms ≥{threshold})"

    floor = int(rules.get("default_min", 1))
    if base < floor:
        base = floor
        rationale += f" (clamped to default_min={floor})"

    return base, rationale


def _vehicle_pick(category: str, area_m2: float, company: dict) -> tuple[str, str]:
    rules = company["vehicle_rules"]
    default_id = rules["by_category"].get(category)
    if default_id is None:
        raise ValueError(f"no vehicle mapping for category={category}")

    upgrade = rules.get("upgrade_if_m2_above")
    if upgrade and area_m2 > float(upgrade["m2"]):
        return upgrade["to"], (
            f"upgraded to {upgrade['to']} because area_m2={area_m2} > {upgrade['m2']}"
        )
    return default_id, f"category default → {default_id}"


def _equipment_pick(category: str, company: dict) -> list[str]:
    return list(company["equipment_rules"]["by_category"].get(category, []))


def _crew_pick(category: str, where: str | None, company: dict) -> dict[str, Any]:
    postcode = _extract_postcode(where)
    crews = company["crews"]
    locations = company["locations"]
    centroids = company.get("postcode_centroids", {})

    skill_matches = [
        (cid, c) for cid, c in crews.items() if category in c.get("skills", [])
    ]
    if not skill_matches:
        raise ValueError(f"no crew has skill={category}")

    def distance_for(crew_obj: dict) -> float | None:
        if not postcode or postcode not in centroids:
            return None
        depot = locations.get(crew_obj.get("homeDepot"))
        if not depot:
            return None
        c = centroids[postcode]
        return round(_haversine_km(depot["lat"], depot["lng"], c["lat"], c["lng"]), 2)

    area_matches = [
        (cid, c) for cid, c in skill_matches
        if postcode and postcode in c.get("serviceArea", [])
    ]
    pool = area_matches or skill_matches
    fallback_used = not area_matches and bool(postcode)

    ranked = sorted(pool, key=lambda item: (distance_for(item[1]) is None, distance_for(item[1]) or 0))
    chosen_id, chosen = ranked[0]
    dist = distance_for(chosen)

    rationale_bits = []
    if postcode:
        rationale_bits.append(f"postcode={postcode}")
        if fallback_used:
            rationale_bits.append("no crew serves this postcode → fell back to nearest skill-match")
        else:
            rationale_bits.append("in service area")
    else:
        rationale_bits.append("no postcode in 'where' → picked first skill-match")
    if dist is not None:
        rationale_bits.append(f"depot distance ≈ {dist} km")

    return {
        "id": chosen_id,
        "name": chosen["name"],
        "homeDepot": chosen["homeDepot"],
        "googleCalendarId": chosen.get("googleCalendarId"),
        "distanceKm": dist if dist is not None else 0.0,
        "rationale": "; ".join(rationale_bits),
    }


def _estimated_minutes_total(area_m2: float, rooms: int, bathrooms: int,
                             category: str, addons: list[str], company: dict) -> int:
    cfg = company["service_types"][category]
    minutes = (
        area_m2 * float(cfg.get("minutes_per_m2", 0))
        + rooms * float(cfg.get("minutes_per_room", 0))
        + bathrooms * float(cfg.get("minutes_per_bathroom", 0))
    )
    for a in addons or []:
        minutes += float(company.get("addons", {}).get(a, {}).get("minutes", 0))
    return int(round(minutes))


def allocate(inputs: dict[str, Any], company: dict[str, Any]) -> dict[str, Any]:
    category = inputs["category"]
    if category not in company["service_types"]:
        raise ValueError(f"unknown category={category}")

    area_m2 = float(inputs["area_m2"])
    rooms = int(inputs.get("rooms", 0))
    bathrooms = int(inputs.get("bathrooms", company["default_values"].get("bathrooms", 1)))
    addons = list(inputs.get("addons", []))

    people, p_rat = _personnel_count(area_m2, rooms, category, company)
    vehicle_id, v_rat = _vehicle_pick(category, area_m2, company)
    equipment_ids = _equipment_pick(category, company)
    crew = _crew_pick(category, inputs.get("where"), company)

    minutes_total = _estimated_minutes_total(area_m2, rooms, bathrooms, category, addons, company)
    wall_clock_minutes = max(1, int(round(minutes_total / max(1, people))))

    return {
        "category": category,
        "personnel": {"count": people, "rationale": p_rat},
        "vehicle": {
            "id": vehicle_id,
            "label": company["vehicles"][vehicle_id]["label"],
            "rationale": v_rat,
        },
        "equipment": [
            {"id": eid, "label": company["equipment"][eid]["label"]}
            for eid in equipment_ids
        ],
        "crew": crew,
        "estimated_minutes_total": minutes_total,
        "wall_clock_minutes": wall_clock_minutes,
    }
