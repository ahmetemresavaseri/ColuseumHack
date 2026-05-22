"""Shared boto3 client factories + identity helpers for the Atrium project."""
from __future__ import annotations

import os
from functools import lru_cache

import boto3
from botocore.config import Config

DEFAULT_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")

_default_config = Config(
    region_name=DEFAULT_REGION,
    retries={"max_attempts": 3, "mode": "standard"},
    user_agent_extra="atrium-hackathon/0.1",
)


def region() -> str:
    return DEFAULT_REGION


@lru_cache(maxsize=1)
def sts():
    return boto3.client("sts", config=_default_config)


@lru_cache(maxsize=1)
def get_account_id() -> str:
    return sts().get_caller_identity()["Account"]


@lru_cache(maxsize=1)
def get_caller_arn() -> str:
    return sts().get_caller_identity()["Arn"]


@lru_cache(maxsize=1)
def bedrock():
    return boto3.client("bedrock", config=_default_config)


@lru_cache(maxsize=1)
def bedrock_runtime():
    return boto3.client(
        "bedrock-runtime",
        config=Config(
            region_name=DEFAULT_REGION,
            retries={"max_attempts": 2, "mode": "standard"},
            read_timeout=60,
        ),
    )


@lru_cache(maxsize=1)
def connect_client():
    return boto3.client("connect", config=_default_config)


@lru_cache(maxsize=1)
def lambda_client():
    return boto3.client("lambda", config=_default_config)


@lru_cache(maxsize=1)
def iam():
    return boto3.client("iam", config=_default_config)


@lru_cache(maxsize=1)
def logs():
    return boto3.client("logs", config=_default_config)
