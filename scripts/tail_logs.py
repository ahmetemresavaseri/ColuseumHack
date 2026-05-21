"""Stream CloudWatch Logs for a log group, like `aws logs tail --follow`.

Usage:
    python scripts/tail_logs.py /aws/lambda/atrium-input-agent
    python scripts/tail_logs.py                                  # defaults to input-agent

Polls the group every 2 s and prints any events newer than the last seen timestamp.
Ctrl-C to stop.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from botocore.exceptions import ClientError

from aws_helpers import logs

DEFAULT_GROUP = "/aws/lambda/atrium-input-agent"


def tail(group: str, start_from_now: bool = True) -> None:
    client = logs()
    start_ms = int(time.time() * 1000) if start_from_now else 0
    print(f"[tail] following {group} from {'now' if start_from_now else 'beginning'} (Ctrl-C to stop)")
    last_seen_ms = start_ms - 1
    while True:
        try:
            resp = client.filter_log_events(
                logGroupName=group,
                startTime=last_seen_ms + 1,
                limit=10000,
            )
        except ClientError as e:
            code = e.response["Error"].get("Code", "")
            if code == "ResourceNotFoundException":
                print(f"[tail] log group {group} doesn't exist yet — waiting ...")
                time.sleep(3)
                continue
            raise
        events = resp.get("events", [])
        if events:
            for ev in events:
                ts = ev["timestamp"]
                msg = ev["message"].rstrip()
                stream = ev["logStreamName"].split("/")[-1][:8]
                print(f"  [{ts}] [{stream}] {msg}")
                last_seen_ms = max(last_seen_ms, ts)
        time.sleep(2)


def main() -> int:
    group = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GROUP
    try:
        tail(group)
    except KeyboardInterrupt:
        print("\n[tail] stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
