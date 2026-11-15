#!/usr/bin/env python3
"""Hook entry point: refresh the library when a chat starts, replies, or ends.

Wired into both clients by install.sh (~/.claude/settings.json SessionStart and
SessionEnd, ~/.codex/hooks.json SessionStart, Stop and SessionEnd). Codex fires
Stop after every reply. All the work -- and every knob --
lives in pipeline.py; this file exists only to be a stable path in those config
files, to read the hook event on stdin, and to never fail loudly.

SessionEnd and Stop run detached: the client may be exiting, and a reply hook
must not hold up the next turn. Detaching is safe since
pipeline.py takes a lock and every stage writes atomically.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.join(HERE, "pipeline.py")


def main():
    # The labelling stage shells out to `claude -p`, which fires these same
    # hooks. pipeline.py exports this into its stages, so a nested hook is a
    # no-op instead of a refresh that only bounces off the lock.
    if os.environ.get("DASHBOARD_REFRESH_ACTIVE"):
        return 0
    try:
        event = json.load(sys.stdin)
    except Exception:
        event = {}

    transcript = str(event.get("transcript_path", ""))
    client = "codex" if "/.codex" in transcript else "claude"
    hook = str(event.get("hook_event_name") or "")
    source = str(event.get("source") or event.get("reason") or "unknown")
    ending = hook == "SessionEnd" or "--session-end" in sys.argv
    replied = hook == "Stop"
    phase = "end" if ending else "reply" if replied else "start"
    reason = f"{client}:{phase}:{'turn' if replied else source}"

    cmd = [sys.executable, PIPELINE, "--reason", reason, "--quiet"]
    try:
        if ending or replied:
            subprocess.Popen(cmd, cwd=HERE, start_new_session=True,
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(cmd, cwd=HERE, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=3000)
    except Exception:
        pass  # a hook must never interrupt opening or closing a chat
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
