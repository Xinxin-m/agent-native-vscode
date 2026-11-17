#!/usr/bin/env python3
"""Add or remove the router and session-end hooks in a Claude Code or Codex settings file.

Merges into the file's "hooks" object: existing hooks are kept, running it twice adds
nothing, and --uninstall removes only the entries that point at these scripts. The old
file is copied to <HARNESS_HOME>/backups/ before every write. --dry-run prints the result.

Claude Code: ~/.claude/settings.json (user) or <repo>/.claude/settings.json (project).
Codex reads the same structure from ~/.codex/hooks.json or <repo>/.codex/hooks.json;
pass that path with --settings, then review and trust the hooks with /hooks in Codex.
"""
import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("HARNESS_HOME", Path.home() / ".library-harness"))
HOOKS = {"UserPromptSubmit": ("router_hook.py", 5), "SessionEnd": ("session_end_hook.py", 5)}


def command(python, script, rules):
    parts = [python, str(HERE / script)]
    if script == "router_hook.py" and rules:
        parts += ["--rules", str(rules)]
    return " ".join(shlex.quote(p) for p in parts)


def ours(handler):
    text = str(handler.get("command", "")).replace("'", "").replace('"', "")
    return any(str(HERE / script) in text for script, _ in HOOKS.values())


def merge(settings, python, rules, uninstall=False):
    hooks = settings.setdefault("hooks", {})
    for event, (script, timeout) in HOOKS.items():
        groups = hooks.get(event, [])
        for group in groups:
            group["hooks"] = [h for h in group.get("hooks", []) if not ours(h)]
        groups = [g for g in groups if g.get("hooks")]
        if not uninstall:
            groups.append({"hooks": [{"type": "command", "command": command(python, script, rules), "timeout": timeout}]})
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks")
    return settings


def manual_steps(python, rules):
    snippet = merge({}, python, rules)
    return ("Manual steps: open the settings file, find or create the top-level \"hooks\" object, and add these\n"
            "entries to the arrays for each event (keep any entries already there):\n" + json.dumps(snippet, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--settings", type=Path, default=Path.home() / ".claude" / "settings.json")
    parser.add_argument("--rules", type=Path, help="rules file passed to the router (default: its own default)")
    parser.add_argument("--python", default=sys.executable, help="interpreter written into the hook command")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    raw = args.settings.read_text() if args.settings.is_file() else "{}"
    try:
        settings = json.loads(raw or "{}")
    except ValueError as error:
        parser.error(f"{args.settings} is not valid JSON ({error}); fix it first, nothing was changed")
    updated = merge(json.loads(json.dumps(settings)), args.python, args.rules, args.uninstall)
    text = json.dumps(updated, indent=2) + "\n"
    if args.dry_run:
        print(text, end="")
    elif updated == settings:
        print(f"No change: {args.settings} is already {'clean' if args.uninstall else 'installed'}.")
    else:
        if args.settings.is_file():
            backup = HOME / "backups" / f"{args.settings.name}.{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10**9:09d}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(raw)
            print(f"Backup: {backup}")
        args.settings.parent.mkdir(parents=True, exist_ok=True)
        temp = args.settings.with_name(args.settings.name + ".tmp")
        temp.write_text(text)
        temp.replace(args.settings)
        print(f"{'Removed from' if args.uninstall else 'Installed in'} {args.settings}")
    if not args.uninstall:
        print(manual_steps(args.python, args.rules), file=sys.stderr)


if __name__ == "__main__":
    main()
