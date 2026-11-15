#!/usr/bin/env python3
"""
pipeline.py — the one way to bring the library up to date.

    parse  ->  label  ->  group
    indexer.py  enrich.py  classify.py

Every caller goes through here, so there is a single definition of stage order,
timeouts, and locking: the Claude/Codex session hooks, the dashboard's Rescan
button, and a human at a shell.

  python3 pipeline.py                 # incremental, labelling capped (hook mode)
  python3 pipeline.py --uncapped      # label the whole backlog
  python3 pipeline.py --full          # re-parse every transcript from scratch
  python3 pipeline.py --parse-only    # deterministic stages only, no model calls
  python3 pipeline.py --reason rescan # tag the log line

Concurrency: one refresh at a time, enforced with a lock file. A hook that
arrives while a refresh is running leaves a marker and exits; the running
refresh then makes one more pass, because the indexer may already have read the
transcript that just changed. An explicit request (--wait, used by the Rescan
button) queues instead, so pressing the button always does the work that was
asked for.

Recursion: the labelling stage shells out to `claude -p`, which itself fires the
session hooks. DASHBOARD_REFRESH_ACTIVE is exported into the stages so those
nested hooks return without doing anything.

Every knob has a config.json key and an environment override, so the hook can
stay a one-liner. Nothing here is specific to one machine or person.
"""
import argparse
import datetime
import fcntl
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
LOCK_PATH = os.path.join(DATA, "refresh.lock")
LOG_PATH = os.path.join(DATA, "refresh.log")
PENDING_PATH = os.path.join(DATA, "refresh.pending")
MAX_PASSES = 3
LOG_MAX_BYTES = 2_000_000


def config():
    try:
        with open(os.path.join(DATA, "config.json")) as fh:
            return json.load(fh)
    except Exception:
        return {}


def setting(cfg, key, default):
    """config.json value, overridable by DASHBOARD_<KEY> in the environment."""
    env = os.environ.get("DASHBOARD_" + key.upper())
    if env not in (None, ""):
        return env
    value = cfg.get(key)
    return default if value in (None, "") else value


def trim_log():
    """Keep the log from growing without bound; drop the oldest half."""
    try:
        if os.path.getsize(LOG_PATH) <= LOG_MAX_BYTES:
            return
        with open(LOG_PATH, errors="ignore") as fh:
            text = fh.read()
        with open(LOG_PATH + ".tmp", "w") as fh:
            fh.write(text[len(text) // 2:])
        os.replace(LOG_PATH + ".tmp", LOG_PATH)
    except OSError:
        pass


def run_stage(log, script, args, timeout):
    log.write(f"--- {script} {' '.join(args)}\n")
    log.flush()
    env = dict(os.environ, DASHBOARD_REFRESH_ACTIVE="1")
    try:
        result = subprocess.run([sys.executable, os.path.join(HERE, script), *args],
                                cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                                timeout=timeout, env=env)
        log.write(f"exit={result.returncode}\n")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        # partial work is kept: every stage writes incrementally and atomically
        log.write(f"timeout after {timeout}s (partial progress kept)\n")
        return False
    except Exception as error:
        log.write(f"error={error!r}\n")
        return False


def refresh(reason="manual", full=False, uncapped=False, parse_only=False, wait=False, keep_missing=False):
    """Run the stages in order. Returns True if the library is fully current."""
    os.makedirs(DATA, exist_ok=True)
    cfg = config()
    # Explicit opt-out is the fresh-install default. Missing preserves legacy installations.
    parse_only = parse_only or cfg.get('enable_model_labeling') is False
    trim_log()

    with open(LOCK_PATH, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # the running refresh sees this marker and makes another pass
            with open(PENDING_PATH, "w") as fh:
                fh.write(reason)
            return True

        ok = True
        for _ in range(MAX_PASSES):
            try:
                os.remove(PENDING_PATH)
            except FileNotFoundError:
                pass
            ok = _run_pass(cfg, reason, full, uncapped, parse_only, keep_missing)
            try:
                with open(PENDING_PATH) as fh:
                    reason = "queued:" + (fh.read().strip() or "hook")
            except FileNotFoundError:
                break
            full = False
        return ok


def _run_pass(cfg, reason, full, uncapped, parse_only, keep_missing):
    """One parse -> label -> group pass. Caller holds the lock."""
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(LOG_PATH, "a") as log:
        log.write(f"\n{stamp} refresh reason={reason}"
                  f"{' full' if full else ''}{' uncapped' if uncapped else ''}"
                  f"{' parse-only' if parse_only else ''}\n")

        index_args = (["--full"] if full else []) + (["--keep-missing"] if keep_missing else [])
        if not run_stage(log, "indexer.py", index_args,
                         int(setting(cfg, "index_timeout", 900))):
            log.write("parse failed; leaving labels and groups alone\n")
            return False
        if parse_only:
            # Delegated transcripts have native labels and inherit their
            # root project, so importing them never creates a model backlog.
            if not run_stage(log, "enrich.py", ["--children-only"],
                             int(setting(cfg, "index_timeout", 900))):
                return False
            return run_stage(log, "workspace_groups.py", [],
                             int(setting(cfg, "index_timeout", 900)))

        limit = "0" if uncapped else str(setting(cfg, "enrich_limit", 120))
        workers = str(setting(cfg, "enrich_workers", 4))
        timeout = int(setting(cfg, "enrich_stage_timeout", 3600 if uncapped else 1200))
        if not run_stage(log, "enrich.py",
                         ["--workers", workers, "--limit", limit], timeout):
            log.write("labelling incomplete; grouping still runs on what exists\n")

        return run_stage(log, "classify.py", [],
                         int(setting(cfg, "classify_stage_timeout", 1800)))


def main():
    parser = argparse.ArgumentParser(description="Refresh the chat library.")
    parser.add_argument("--reason", default="manual",
                        help="tag for the log line (hook, session-end, rescan...)")
    parser.add_argument("--full", action="store_true", help="re-parse every transcript")
    parser.add_argument("--uncapped", action="store_true", help="label the whole backlog")
    parser.add_argument("--parse-only", action="store_true", help="no model calls")
    parser.add_argument("--keep-missing", action="store_true", help="retain history whose source is unavailable")
    parser.add_argument("--wait", action="store_true",
                        help="queue behind a running refresh instead of skipping")
    parser.add_argument("--quiet", action="store_true", help="say nothing on stdout")
    args = parser.parse_args()

    ok = refresh(reason=args.reason, full=args.full, uncapped=args.uncapped,
                 parse_only=args.parse_only, wait=args.wait, keep_missing=args.keep_missing)
    if not args.quiet:
        print(("library is current" if ok else
               "refresh incomplete -- see data/refresh.log") + f" ({args.reason})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
