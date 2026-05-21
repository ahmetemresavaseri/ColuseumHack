"""Smoke test for tenant KB retrieval."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambdas" / "input_agent"))

from kb import kb_lookup


def main() -> int:
    company_id = os.environ.get("COMPANY_ID", "glanz-ag")
    question = "How much is move-out cleaning per square meter?"
    print(json.dumps(kb_lookup(question, company_id), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
