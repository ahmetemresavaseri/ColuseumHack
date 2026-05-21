"""Run the Brain pricing helper locally with seed price data."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambdas" / "brain"))

from pricing import estimate_price


def main() -> int:
    matrix_rows = json.loads((ROOT / "infrastructure" / "seed" / "price_matrix.json").read_text())
    price_matrix = {row["serviceType"]: row for row in matrix_rows}
    slots = {
        "what": "move out",
        "area": 85,
        "rooms": 4,
        "urgency": "urgent",
        "when": "tomorrow",
        "email": "customer@example.com",
    }
    print(json.dumps(estimate_price(slots, price_matrix), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
