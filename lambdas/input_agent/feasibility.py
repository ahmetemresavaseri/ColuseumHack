"""Phase 2 feasibility assessment.

Given the extracted slots + the Brain's price estimate, decide whether the
booking is `bookable`, `needs_review`, or `unsupported`. Returns reason codes
so the wall / operator can see WHY without re-running the logic.

Lives inside the Brain Lambda so we get it for free on every `compute_price`
call. A mirror copy in `lambdas/input_agent/feasibility.py` lets the Lex hook
invoke it as a standalone `feasibility_assessment` tool without round-tripping
through Brain.
"""
from __future__ import annotations

from typing import Any

# Heuristics — tune per tenant later via `Companies` config.
LARGE_AREA_THRESHOLD_M2 = 500
LARGE_ROOMS_THRESHOLD = 12


def assess(
    slots: dict[str, Any],
    brain: dict[str, Any],
    crews: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return `{status, reasons, confidence}`.

    status   ∈ {"bookable", "needs_review", "unsupported"}
    reasons  list of short codes — empty when bookable
    confidence  rough heuristic 0.0–1.0
    """
    crews = crews or []
    reasons: list[str] = []

    # Unsupported: Brain couldn't price the job at all.
    brain_status = brain.get("status")
    if brain_status == "needs_service_type":
        return {"status": "unsupported", "reasons": ["unknown_service"], "confidence": 0.95}
    if brain_status == "missing_price_matrix":
        return {
            "status": "unsupported",
            "reasons": ["service_not_offered"],
            "confidence": 0.95,
        }

    # Needs review heuristics.
    if not brain.get("crew") and not crews:
        reasons.append("no_crew_assigned")
    if brain.get("needsPhotos"):
        reasons.append("photos_required")

    area = _as_number(slots.get("area"))
    if area is not None and area > LARGE_AREA_THRESHOLD_M2:
        reasons.append("large_area")

    rooms = _as_number(slots.get("rooms"))
    if rooms is not None and rooms > LARGE_ROOMS_THRESHOLD:
        reasons.append("large_rooms")

    if brain.get("crew"):
        # Capacity check: estimated hours vs daily crew capacity.
        try:
            hours = float(brain.get("estimatedHours") or 0)
            capacity = float(brain.get("crew", {}).get("capacityHoursPerDay") or 0)
            if hours and capacity and hours > capacity:
                reasons.append("over_capacity")
        except (TypeError, ValueError):
            pass

    if reasons:
        return {"status": "needs_review", "reasons": reasons, "confidence": 0.7}
    return {"status": "bookable", "reasons": [], "confidence": 0.9}


def _as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        import re
        m = re.search(r"-?\d+(?:\.\d+)?", str(value))
        return float(m.group(0)) if m else None
