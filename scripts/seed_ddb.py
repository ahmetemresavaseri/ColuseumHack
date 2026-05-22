"""Seed DynamoDB tables from infrastructure/seed JSON files."""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "infrastructure" / "seed"


def _table(env_name: str, fallback: str):
    return boto3.resource("dynamodb").Table(os.environ.get(env_name, fallback))


def put_all(table_name: str, records: list[dict]) -> None:
    table = _table(table_name, f"atrium-{table_name.removesuffix('_TABLE').lower().replace('_', '-')}")
    with table.batch_writer() as batch:
        for item in records:
            batch.put_item(Item=item)
    print(f"[OK] Seeded {len(records)} records into {table.table_name}")


def _load(name: str) -> list[dict]:
    return json.loads((SEED / name).read_text(), parse_float=Decimal)


def main() -> int:
    put_all("COMPANIES_TABLE", _load("companies.json"))
    put_all("CREWS_TABLE", _load("crews.json"))
    put_all("PRICE_MATRIX_TABLE", _load("price_matrix.json"))
    put_all("KNOWLEDGE_ITEMS_TABLE", _load("knowledge_items.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
