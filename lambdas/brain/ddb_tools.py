"""DynamoDB reads for Brain tools."""
from __future__ import annotations

import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key


def _table(env_name: str, fallback: str):
    return boto3.resource("dynamodb").Table(os.environ.get(env_name, fallback))


def get_available_crews(company_id: str) -> list[dict[str, Any]]:
    table = _table("CREWS_TABLE", "atrium-crews")
    response = table.query(
        KeyConditionExpression=Key("companyId").eq(company_id),
    )
    return response.get("Items", [])


def get_price_matrix(company_id: str) -> dict[str, Any]:
    table = _table("PRICE_MATRIX_TABLE", "atrium-price-matrix")
    response = table.query(
        KeyConditionExpression=Key("companyId").eq(company_id),
    )
    return {item["serviceType"]: item for item in response.get("Items", [])}
