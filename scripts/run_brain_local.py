"""Run the Brain pricing helper locally with seed price data."""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambdas" / "brain"))

from pricing import estimate_price

SEED = ROOT / "infrastructure" / "seed"


def _load(name: str):
    return json.loads((SEED / name).read_text(), parse_float=Decimal)


def _pretty(payload) -> str:
    return json.dumps(payload, indent=2, default=str)


def _filter(rows, company_id):
    return [r for r in rows if r.get("companyId") == company_id]


def run_legacy_glanz() -> None:
    print("\n=== glanz-ag (legacy flat schema) ===")
    matrix = {r["serviceType"]: r for r in _filter(_load("price_matrix.json"), "glanz-ag")}
    slots = {
        "what": "move out",
        "area": 85,
        "rooms": 4,
        "urgency": "urgent",
        "when": "tomorrow",
        "email": "customer@example.com",
    }
    print(_pretty(estimate_price(slots, matrix)))


def run_rich_atrium() -> None:
    print("\n=== atrium-demo (rich schema, sample_input from company.json) ===")
    matrix = {r["serviceType"]: r for r in _filter(_load("price_matrix.json"), "atrium-demo")}
    companies = _load("companies.json")
    company = next(c for c in companies if c["companyId"] == "atrium-demo")
    slots = {
        "what": "move_out_cleaning",
        "area_m2": 75,
        "rooms": 3,
        "bathrooms": 1,
        "urgency": "standard",
        "condition": "normal",
        "addons": ["oven", "blinds"],
        "email": "customer@example.com",
        "preferred_date": "2026-05-29",
        "postcode": "8001",
    }
    print(_pretty(estimate_price(slots, matrix, rules=company)))


def main() -> int:
    run_legacy_glanz()
    run_rich_atrium()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
