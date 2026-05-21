"""Seed DynamoDB tables from infrastructure/seed JSON files."""
from __future__ import annotations

import json
import os
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


def main() -> int:
    put_all("COMPANIES_TABLE", json.loads((SEED / "companies.json").read_text()))
    put_all("CREWS_TABLE", json.loads((SEED / "crews.json").read_text()))
    put_all("PRICE_MATRIX_TABLE", json.loads((SEED / "price_matrix.json").read_text()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
