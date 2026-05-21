"""Brain Lambda - Claude Sonnet 4.6 with tool-use for crew/price.

Internal tools: get_available_crews, get_price_matrix.
See README.md section "Modules > 2. Brain (Live Pricing)".
"""
from __future__ import annotations

from typing import Any

from ddb_tools import get_available_crews, get_price_matrix
from pricing import estimate_price


def handler(event: dict[str, Any], context) -> dict[str, Any]:
    company_id = event.get("companyId", "glanz-ag")
    slots = event.get("slots", event)

    price_matrix = event.get("priceMatrix") or get_price_matrix(company_id)
    crews = event.get("crews") or get_available_crews(company_id)
    estimate = estimate_price(slots, price_matrix)

    service_type = estimate.get("serviceType")
    matching_crews = [
        crew for crew in crews if not service_type or service_type in crew.get("skills", [])
    ]

    return {
        **estimate,
        "companyId": company_id,
        "crew": matching_crews[0] if matching_crews else None,
    }
