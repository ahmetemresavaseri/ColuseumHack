from __future__ import annotations

from feasibility import assess


def test_bookable_with_crew_no_photos():
    out = assess(
        slots={"what": "MOVE_OUT_CLEANING", "area": 85, "rooms": 4},
        brain={
            "status": "estimated",
            "serviceType": "MOVE_OUT_CLEANING",
            "price": 500,
            "currency": "CHF",
            "needsPhotos": False,
            "crew": {"crewId": "c1", "capacityHoursPerDay": 8},
        },
        crews=[{"crewId": "c1"}],
    )
    assert out["status"] == "bookable"
    assert out["reasons"] == []


def test_needs_review_when_photos_required():
    out = assess(
        slots={"what": "WINDOW_CLEANING", "area": 30, "rooms": 2},
        brain={
            "status": "estimated",
            "serviceType": "WINDOW_CLEANING",
            "needsPhotos": True,
            "crew": {"crewId": "c1", "capacityHoursPerDay": 8},
        },
        crews=[{"crewId": "c1"}],
    )
    assert out["status"] == "needs_review"
    assert "photos_required" in out["reasons"]


def test_needs_review_when_no_crew():
    out = assess(
        slots={"what": "OFFICE_CLEANING", "area": 50},
        brain={"status": "estimated", "serviceType": "OFFICE_CLEANING", "needsPhotos": False},
        crews=[],
    )
    assert out["status"] == "needs_review"
    assert "no_crew_assigned" in out["reasons"]


def test_unsupported_when_no_service_type():
    out = assess(
        slots={},
        brain={"status": "needs_service_type"},
        crews=[],
    )
    assert out["status"] == "unsupported"
    assert "unknown_service" in out["reasons"]


def test_unsupported_when_no_price_matrix():
    out = assess(
        slots={"what": "OFFICE_CLEANING"},
        brain={"status": "missing_price_matrix", "serviceType": "OFFICE_CLEANING"},
        crews=[],
    )
    assert out["status"] == "unsupported"


def test_large_area_triggers_review():
    out = assess(
        slots={"what": "OFFICE_CLEANING", "area": "800 m2"},
        brain={
            "status": "estimated",
            "serviceType": "OFFICE_CLEANING",
            "needsPhotos": False,
            "crew": {"crewId": "c1", "capacityHoursPerDay": 8},
        },
        crews=[{"crewId": "c1"}],
    )
    assert out["status"] == "needs_review"
    assert "large_area" in out["reasons"]
