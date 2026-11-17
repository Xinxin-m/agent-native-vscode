#!/usr/bin/env python3
"""SessionEnd hook: queue the finished session for the learner agent.

Reads the hook JSON on stdin and appends one line to <HARNESS_HOME>/data/learner_queue.jsonl:
{"ts", "session_id", "transcript_path", "cwd", "reason"}. No model calls, no network.
It always exits 0 and prints nothing, so it can never fail or delay the session.
"""
import json
import os
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("HARNESS_HOME", Path.home() / ".library-harness"))


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__.strip())
        return
    try:
        event = json.load(sys.stdin)
        if not event.get("session_id"):
            return
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "session_id": event["session_id"],
                 "transcript_path": event.get("transcript_path"), "cwd": event.get("cwd"), "reason": event.get("reason")}
        queue = HOME / "data" / "learner_queue.jsonl"
        queue.parent.mkdir(parents=True, exist_ok=True)
        with open(queue, "a") as handle:
            handle.write(json.dumps(entry) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
