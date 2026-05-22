"""Test bootstrap: put the Input Agent module dir on sys.path.

The Lambda is laid out flat (no package init) the way AWS Lambda expects, so
the tests have to add the directory to `sys.path` explicitly.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT_AGENT = ROOT / "lambdas" / "input_agent"
if str(INPUT_AGENT) not in sys.path:
    sys.path.insert(0, str(INPUT_AGENT))
