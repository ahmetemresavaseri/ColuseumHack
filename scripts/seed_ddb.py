"""Seed DynamoDB tables from infrastructure/seed JSON files."""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "infrastructure" / "seed"


# Fallbacks must match the CDK names in infrastructure/stacks/data_stack.py.
TABLE_FALLBACKS = {
    "COMPANIES_TABLE": "atrium-companies",
    "CREWS_TABLE": "atrium-crews",
    "PRICE_MATRIX_TABLE": "atrium-pricematrix",
}


def _table(env_name: str):
    fallback = TABLE_FALLBACKS[env_name]
    return boto3.resource("dynamodb").Table(os.environ.get(env_name, fallback))


def _load(filename: str) -> list[dict]:
    # DynamoDB rejects native float; parse_float=Decimal keeps precision.
    return json.loads((SEED / filename).read_text(), parse_float=Decimal)


def put_all(env_name: str, records: list[dict]) -> None:
    table = _table(env_name)
    with table.batch_writer() as batch:
        for item in records:
            batch.put_item(Item=item)
    print(f"[OK] Seeded {len(records)} records into {table.table_name}")


def main() -> int:
    put_all("COMPANIES_TABLE", _load("companies.json"))
    put_all("CREWS_TABLE", _load("crews.json"))
    put_all("PRICE_MATRIX_TABLE", _load("price_matrix.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
