#!/usr/bin/env python3
"""UserPromptSubmit hook: inject the rules whose pattern matches the user's prompt.

Hook mode (default) reads the hook JSON on stdin, matches the "prompt" field against a
rules file, and prints {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
"additionalContext": ...}} when at least one rule matches. Claude Code and Codex both
accept this output. Any error exits 0 with no output, so the prompt is never blocked.

Rules file (--rules, env HARNESS_RULES, default <HARNESS_HOME>/router_rules.json):
  [{"name": "...", "pattern": "case-insensitive regex", "rule": "text to inject",
    "skill": "optional skill name", "agent": "optional agent name"}]

--test replays recent human prompts (via session_metrics.py) and reports how often
each pattern fires. A pattern above --max-rate (default 15%) is too broad; the exit
code is 1 when any pattern is too broad or fails to compile.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("HARNESS_HOME", Path.home() / ".library-harness"))
RULES = Path(os.environ.get("HARNESS_RULES", HOME / "router_rules.json"))
MAX_RULES = 4


def load_rules(path):
    rules = []
    for item in json.loads(Path(path).read_text()):
        try:
            rules.append(dict(item, regex=re.compile(item["pattern"], re.I)))
        except (re.error, KeyError, TypeError) as error:
            rules.append(dict(item, regex=None, error=str(error)))
    return rules


def matches(rules, prompt):
    text = prompt[:6000]
    return [r for r in rules if r.get("regex") and r["regex"].search(text)][:MAX_RULES]


def context(hits):
    lines = []
    for rule in hits:
        line = f"Rule ({rule.get('name', 'unnamed')}): {rule.get('rule', '').strip()}"
        if rule.get("skill"):
            line += f" Use the `{rule['skill']}` skill."
        if rule.get("agent"):
            line += f" Consider dispatching the `{rule['agent']}` agent."
        lines.append(line)
    return "\n".join(lines)


def hook(rules_path):
    if os.environ.get("HARNESS_ROUTER_OFF"):
        return
    event = json.load(sys.stdin)
    prompt = (event.get("prompt") or "").strip()
    if not prompt or prompt.startswith("/"):
        return  # slash commands already name what they run
    hits = matches(load_rules(rules_path), prompt)
    if not hits:
        return
    try:
        log = HOME / "data" / "router_log.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as handle:
            handle.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "session": event.get("session_id"),
                                     "rules": [r.get("name") for r in hits]}) + "\n")
    except OSError:
        pass
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context(hits)}}))


def replay(rules, prompts, max_rate):
    """Return report lines and whether every pattern passed."""
    total, ok = len(prompts), True
    lines = [f"{total} prompts replayed", "", "| Rule | Fires | Rate | Status |", "| --- | --- | --- | --- |"]
    for rule in rules:
        name = rule.get("name", rule.get("pattern"))
        if rule.get("regex") is None:
            ok = False
            lines.append(f"| {name} | - | - | invalid: {rule.get('error')} |")
            continue
        fires = sum(1 for p in prompts if rule["regex"].search(p[:6000]))
        rate = fires / total if total else 0.0
        status = "too broad" if rate > max_rate else ("never fired" if fires == 0 else "ok")
        ok = ok and rate <= max_rate
        lines.append(f"| {name} | {fires} | {rate:.1%} | {status} |")
    return lines, ok


def recent_prompts(days, claude_dir, codex_dir):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import session_metrics as sm
    since = dt.datetime.now().astimezone() - dt.timedelta(days=days)
    rows = [sm.parse_claude(p) for p in sm.transcripts(claude_dir or sm.CLAUDE_DIR, since)]
    rows += [sm.parse_codex(p) for p in sm.transcripts(codex_dir or sm.CODEX_DIR, since)]
    return [p["text"] for r in rows if not r["automated"] for p in r["_prompts"]
            if p["ts"] and p["ts"] >= since and not p["text"].startswith("/")]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rules", type=Path, default=RULES)
    parser.add_argument("--test", action="store_true", help="replay recent prompts and report firing rates")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-rate", type=float, default=0.15)
    parser.add_argument("--claude-dir", type=Path)
    parser.add_argument("--codex-dir", type=Path)
    args = parser.parse_args(argv)
    if not args.test:
        try:
            hook(args.rules)
        except Exception:
            pass
        return 0
    lines, ok = replay(load_rules(args.rules), recent_prompts(args.days, args.claude_dir, args.codex_dir), args.max_rate)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
