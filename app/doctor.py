#!/usr/bin/env python3
"""
doctor.py — is this installation healthy, and if not, what is the fix?

    python3 doctor.py            # report
    python3 doctor.py --fix      # also repair what can be repaired safely

Checks, in the order things actually break:
  1. python + the CLIs the pipeline shells out to
  2. transcript roots and config
  3. hook wiring in ~/.claude/settings.json and ~/.codex/hooks.json
  4. data files parse (a truncated index.json is the classic failure)
  5. library health: unlabelled, failed labels, stale labels, ungrouped
  6. the server: running and answering
  7. leftovers: orphan cleaned copies, huge logs

--fix only does the reversible things: create config.json from the example,
wire missing hooks, rebuild a corrupt index. It never deletes chat data and
never touches a transcript.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clis  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CHATS = os.path.join(DATA, "chats")
HOME = os.path.expanduser("~")
CLAUDE_SETTINGS = os.path.join(HOME, ".claude", "settings.json")
CODEX_HOOKS = os.path.join(HOME, ".codex", "hooks.json")
HOOK_SCRIPT = os.path.join(HERE, "session_start_refresh.py")

OK, WARN, BAD = "ok", "warn", "bad"
MARK = {OK: "  ok  ", WARN: " warn ", BAD: " FAIL "}
results = []


def report(state, what, detail="", fix=""):
    results.append((state, what, detail, fix))


def load(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except Exception:
        return "CORRUPT"


def config():
    cfg = load(os.path.join(DATA, "config.json"), {}) or {}
    return {} if cfg == "CORRUPT" else cfg


# ---------------------------------------------------------------- 1. binaries
def check_tools():
    if sys.version_info < (3, 11):
        report(BAD, "python", f"{sys.version.split()[0]} (need 3.11+)")
    else:
        report(OK, "python", sys.version.split()[0])
    for name, why in (("claude", "label/group chats and resume Claude sessions"),
                      ("codex", "resume Codex sessions")):
        resolved = clis.cli(name)
        if os.sep in resolved and os.access(resolved, os.X_OK):
            # A service starts with a minimal PATH. Note when only the widened
            # search finds it: that is the difference between the pipeline
            # working in your shell and failing under the login service.
            extra = "" if shutil.which(name) else "  (found off PATH -- clis.py handles this)"
            report(OK, f"{name} CLI", resolved + extra)
        else:
            report(WARN, f"{name} CLI", f"not found anywhere on the search path -- cannot {why}",
                   f'set "{name}_bin" in data/config.json to its full path')


# ------------------------------------------------------- 2. roots and config
def check_config(fix):
    cfg_path = os.path.join(DATA, "config.json")
    example = os.path.join(DATA, "config.example.json")
    cfg = load(cfg_path)
    if cfg is None:
        if fix and os.path.exists(example):
            shutil.copy(example, cfg_path)
            report(OK, "config.json", "created from config.example.json")
        else:
            report(BAD, "config.json", "missing",
                   "cp data/config.example.json data/config.json")
        cfg = config()
    elif cfg == "CORRUPT":
        report(BAD, "config.json", "not valid JSON", "fix or delete it, then rerun")
        cfg = {}
    else:
        report(OK, "config.json", f"{len(cfg)} keys")

    for key, default in (("claude_projects_root", "~/.claude/projects"),
                         ("codex_sessions_root", "~/.codex/sessions")):
        root = os.path.expanduser(os.path.expandvars(str(cfg.get(key) or default)))
        if os.path.isdir(root):
            n = sum(len([f for f in files if f.endswith(".jsonl")])
                    for _, _, files in os.walk(root))
            report(OK, key, f"{root} ({n} transcripts)")
        else:
            report(WARN, key, f"{root} does not exist -- that client's chats are skipped")
    if not cfg.get("user_context"):
        report(WARN, "user_context", "unset -- titles and projects will be generic",
               "describe yourself in data/config.json")
    return cfg


# -------------------------------------------------------------- 3. hook wiring
def hook_present(blob, event, script):
    for group in ((blob or {}).get("hooks", {}) or {}).get(event, []) or []:
        for hook in group.get("hooks", []) or []:
            if script in str(hook.get("command", "")):
                return True
    return False


def wire(blob, event, matcher, script, timeout):
    blob.setdefault("hooks", {}).setdefault(event, []).append({
        "matcher": matcher,
        "hooks": [{"type": "command",
                   "command": f'python3 "{script}"',
                   "async": True, "timeout": timeout}],
    })


def check_hooks(fix):
    wanted = [
        (CLAUDE_SETTINGS, "SessionStart", "startup|resume|clear", 2400, BAD),
        (CLAUDE_SETTINGS, "SessionEnd", "*", 60, WARN),
        (CODEX_HOOKS, "SessionStart", "^startup$", 2400, WARN),
        (CODEX_HOOKS, "Stop", "*", 60, WARN),
        (CODEX_HOOKS, "SessionEnd", "*", 3, WARN),
    ]
    for path, event, matcher, timeout, severity in wanted:
        blob = load(path)
        label = f"{os.path.basename(os.path.dirname(path))} {event}"
        if blob == "CORRUPT":
            report(BAD, label, f"{path} is not valid JSON", "fix that file by hand")
            continue
        if blob is None:
            blob = {}
        if hook_present(blob, event, HOOK_SCRIPT):
            report(OK, label, "wired")
            continue
        if fix:
            wire(blob, event, matcher, HOOK_SCRIPT, timeout)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(blob, fh, indent=2)
            os.replace(tmp, path)
            report(OK, label, "wired now (was missing)")
        else:
            report(severity, label, "not wired -- refreshes will not happen on their own",
                   "./install.sh --hooks-only   (or: python3 doctor.py --fix)")


# --------------------------------------------------------------- 4/5. library
def _labelled_users(rec):
    if isinstance(rec.get("labelled_n_user"), int):
        return rec["labelled_n_user"]
    try:
        return int(str(rec.get("content_sig", "")).split(":")[0])
    except ValueError:
        return 10 ** 9  # unsigned legacy label: adopted for free on the next run, not stale


def check_library(fix):
    index = load(os.path.join(DATA, "index.json"), {})
    if index == "CORRUPT":
        if fix:
            subprocess.run([sys.executable, os.path.join(HERE, "indexer.py"), "--full"],
                           cwd=HERE, capture_output=True, timeout=1800)
            index = load(os.path.join(DATA, "index.json"), {}) or {}
            report(OK, "index.json", "was corrupt; rebuilt with --full")
        else:
            report(BAD, "index.json", "not valid JSON (truncated write)",
                   "python3 indexer.py --full")
            return
    if not index:
        report(WARN, "index.json", "no chats indexed yet", "python3 pipeline.py --uncapped")
        return
    report(OK, "index.json", f"{len(index)} chats")

    enrich = load(os.path.join(DATA, "enrich.json"), {}) or {}
    if enrich == "CORRUPT":
        report(BAD, "enrich.json", "not valid JSON", "delete it to relabel from scratch")
        enrich = {}
    roots = {k: m for k, m in index.items() if not m.get('is_child')}
    report(OK, 'delegated transcripts', f'{len(index) - len(roots)} (deterministic labels and inherited projects)')
    missing = [k for k in roots if k not in enrich]
    failed = [k for k, v in enrich.items() if k in roots and isinstance(v, dict) and "error" in v]
    stale = [k for k, m in roots.items()
             if k in enrich and isinstance(enrich[k], dict)
             and not enrich[k].get("provisional") and enrich[k].get("model") != "deterministic"
             and (m.get("n_user") or 0) > _labelled_users(enrich[k])]
    fallback = [k for k, v in enrich.items() if isinstance(v, dict) and v.get("fallback")]
    for label, keys, hint in (("unlabelled chats", missing, "python3 pipeline.py --uncapped"),
                              ("failed labels", failed, "python3 enrich.py --first"),
                              ("labels waiting for the daily update", stale, "python3 pipeline.py --daily")):
        report(OK if not keys else WARN, label, str(len(keys)), hint if keys else "")
    if fallback:
        report(OK, "local fallback labels", f"{len(fallback)} (model would not label these)")

    proj = load(os.path.join(DATA, "projects.json"), {}) or {}
    if proj == "CORRUPT":
        report(BAD, "projects.json", "not valid JSON", "delete it to regroup from scratch")
    else:
        assigned = proj.get("assignments", {}) or {}
        ungrouped = [k for k in roots if k not in assigned]
        report(OK if not ungrouped else WARN, "ungrouped chats", str(len(ungrouped)),
               "python3 classify.py" if ungrouped else "")
        report(OK, "projects", ", ".join(p["name"] for p in proj.get("projects", [])) or "none")

    no_answer = [m for m in roots.values() if m.get("n_answer", 1) < 1]
    if no_answer:
        report(WARN, "indexed chats with no answer", str(len(no_answer)),
               "raise min_answer_msgs, or python3 indexer.py --full")


def check_daily_label(cfg):
    """The once-a-day relabel is a launchd agent (macOS) or user timer (Linux)."""
    label = "com.chatdashboard.daily-label"
    want = str(cfg.get("daily_label_time") or "04:45")
    if sys.platform == "darwin":
        plist = os.path.join(HOME, "Library", "LaunchAgents", label + ".plist")
        loaded = subprocess.run(["launchctl", "list", label], capture_output=True).returncode == 0
        text = open(plist).read() if os.path.exists(plist) else ""
        h, _, m = want.partition(":")
        scheduled = f"<integer>{int(h or 4)}</integer>" in text and f"<key>Minute</key><integer>{int(m or 0)}</integer>" in text
    else:
        loaded = subprocess.run(["systemctl", "--user", "is-enabled", label + ".timer"], capture_output=True).returncode == 0
        text, scheduled = "x", True
    state = load(os.path.join(DATA, "label_state.json"), {}) or {}
    last = state.get("last_daily") if isinstance(state, dict) else None
    if not loaded or not text:
        report(WARN, "daily relabel job", "not installed: chats are labelled once and never refreshed",
               "./install.sh --daily-label")
    elif not scheduled:
        report(WARN, "daily relabel job", f"installed but not at daily_label_time {want}", "./install.sh --daily-label")
    else:
        report(OK, "daily relabel job", f"scheduled {want} local; last run {last or 'never'}")


# ------------------------------------------------------------------ 6. server
def check_server(cfg):
    port = int(cfg.get("port") or 8744)
    url = f"http://127.0.0.1:{port}/api/state"
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            payload = json.load(response)
        served = (payload.get("config") or {}).get("data_dir")
        if not served or os.path.realpath(served) != os.path.realpath(DATA):
            report(BAD, "server", f"port {port} is answered by another Library install"
                   + (f" (data at {served})" if served else " that does not report its data folder"),
                   f"stop that install or set a free \"port\" in {os.path.join(DATA, 'config.json')}")
            return
        report(OK, "server", f"this checkout is answering on {port} ({len(payload.get('chats', []))} chats visible)")
        if "compose_options" not in payload:
            report(WARN, "server build", "running older code than this checkout",
                   "restart it: ./run.sh")
        # The server resumes chats and launches refreshes, so it -- not this
        # shell -- has to be able to see the CLIs.
        cli_check = payload.get("cli_paths") or {}
        if cli_check:
            broken = [n for n, p in cli_check.items() if os.sep not in str(p)]
            report(OK if not broken else WARN, "server can see the CLIs",
                   ", ".join(f"{n}={p}" for n, p in cli_check.items()),
                   "restart the server from a shell where they resolve" if broken else "")
    except Exception as error:
        report(WARN, "server", f"not answering on {port} ({type(error).__name__})", "./run.sh")


# --------------------------------------------------------------- 7. leftovers
def check_leftovers():
    index = load(os.path.join(DATA, "index.json"), {}) or {}
    if index == "CORRUPT" or not os.path.isdir(CHATS):
        return
    orphans, bytes_ = [], 0
    for name in os.listdir(CHATS):
        if name.endswith(".json") and name[:-5] not in index:
            orphans.append(name)
            bytes_ += os.path.getsize(os.path.join(CHATS, name))
    if orphans:
        report(WARN, "orphan cleaned copies", f"{len(orphans)} files, {bytes_ // 1024} KB",
               "python3 indexer.py --prune")
    for name in ("refresh.log", "session-start.log", "enrich.log", "watcher.log"):
        path = os.path.join(DATA, name)
        if os.path.exists(path) and os.path.getsize(path) > 5_000_000:
            report(WARN, f"{name}", f"{os.path.getsize(path) // 1_000_000} MB", "truncate it")


def main():
    fix = "--fix" in sys.argv
    check_tools()
    cfg = check_config(fix)
    check_hooks(fix)
    check_library(fix)
    check_daily_label(cfg)
    check_server(cfg)
    check_leftovers()

    width = max(len(w) for _, w, _, _ in results) + 2
    print(f"\nchat dashboard doctor{'  (--fix applied)' if fix else ''}\n")
    for state, what, detail, hint in results:
        print(f"[{MARK[state]}] {what.ljust(width)}{detail}")
        if hint:
            print(f"{'':>{width + 9}}-> {hint}")
    bad = sum(1 for r in results if r[0] == BAD)
    warn = sum(1 for r in results if r[0] == WARN)
    print(f"\n{len(results)} checks, {bad} failing, {warn} warnings")
    if bad and not fix:
        print("try: python3 doctor.py --fix")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
