"""Upload seed KB documents to the configured S3 bucket."""
from __future__ import annotations

import os
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "infrastructure" / "seed"
DOCS = ("faq.md", "pricelist.md", "service_catalog.md")


def main() -> int:
    bucket = os.environ.get("KB_BUCKET")
    company_id = os.environ.get("COMPANY_ID", "glanz-ag")
    if not bucket:
        raise SystemExit("[FAIL] Set KB_BUCKET before running seed_kb.py")

    s3 = boto3.client("s3")
    for doc in DOCS:
        key = f"companies/{company_id}/{doc}"
        s3.upload_file(str(SEED / doc), bucket, key)
        print(f"[OK] Uploaded s3://{bucket}/{key}")
    print("[..] Start the Bedrock KB ingestion job after upload.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
