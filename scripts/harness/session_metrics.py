#!/usr/bin/env python3
"""Count per-session activity in Claude Code and Codex transcripts. Reads only.

Writes one JSON line per session to <HARNESS_HOME>/data/metrics.jsonl (or --out):
user prompts, assistant turns, tool calls, subagent dispatches, wall time, and two
heuristic signals. Rows already in the output file for sessions outside the new run
are kept, so history survives after old transcripts are cleaned up.

Correction heuristic: a user prompt that follows an assistant turn within
--correction-minutes and either opens with a negation ("no", "wrong", "still", "undo",
"don't"...) or contains a re-ask phrase in its first 300 characters ("I said", "you
forgot", "not what I asked", "why did you", "how many times"...). English only. It misses
polite or indirect corrections and flags some harmless openers ("no rush"). Read it as a
trend, never as a per-session truth.

Repeat heuristic: a prompt of 5 to 80 words whose word set has Jaccard similarity at
least --repeat-threshold with a prompt from an earlier, different session in the same
run. It catches restated instructions and also a user's legitimate habitual requests.

Message text never appears in the output unless --show-text is given.
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

HOME = Path(os.environ.get("HARNESS_HOME", Path.home() / ".library-harness"))
CLAUDE_DIR = Path(os.environ.get("HARNESS_CLAUDE_DIR", Path.home() / ".claude" / "projects"))
CODEX_DIR = Path(os.environ.get("HARNESS_CODEX_DIR", Path.home() / ".codex" / "sessions"))

INJECTED = re.compile(r"<(system-reminder|environment_context|user_instructions|ide_selection|ide_opened_file|"
                      r"command-message|local-command-stdout|local-command-caveat)>.*?</\1>", re.S)
SKIP_PREFIX = ("<command-name>", "<local-command", "<task-notification>", "Caveat:", "[Request interrupted",
               "This session is being continued", "<turn_aborted>", "# AGENTS.md instructions", "<user_instructions>")
CORRECTION = re.compile(r"^\W*(no\b|nope|not\s+(what|that|quite)|wrong|that'?s\s+(wrong|not)|still\b|again\b|"
                        r"i\s+(already\s+)?(said|told|asked)|as\s+i\s+said|you\s+(forgot|missed|ignored|didn'?t)|"
                        r"why\s+(did|didn'?t)\s+you|undo|revert|stop\b|don'?t\b|do\s+not\b|that\s+is\s+not)", re.I)
REASK = re.compile(r"\b(i\s+(already\s+)?(said|told\s+you|asked\s+you)|as\s+i\s+said|not\s+what\s+i\s+(asked|wanted|meant)|"
                   r"you\s+(forgot|missed|ignored|didn'?t\s+(do|fix|follow))|still\s+(not|wrong|broken|doesn'?t|don'?t)|"
                   r"why\s+(did|didn'?t)\s+you|how\s+many\s+times|once\s+again|try\s+again)\b", re.I)
WORD = re.compile(r"[a-z0-9']+")


def stamp(value):
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def clean(text):
    text = INJECTED.sub("", text)
    text = re.sub(r"# Context from my IDE setup:.*?## My request for Codex:", "", text, flags=re.S)
    text = text.strip()
    return "" if not text or text.startswith(SKIP_PREFIX) else text


def new_row(tool, path):
    return {"tool": tool, "session": path.stem, "file": str(path), "cwd": None, "start": None, "end": None,
            "user_prompts": 0, "assistant_turns": 0, "tool_calls": 0, "subagents": 0, "automated": False,
            "_prompts": [], "_last_assistant": None}


def add_prompt(row, when, text):
    row["user_prompts"] += 1
    row["_prompts"].append({"ts": when, "text": text, "after": row["_last_assistant"]})


def touch(row, when):
    if when:
        row["start"] = min(row["start"] or when, when)
        row["end"] = max(row["end"] or when, when)


def parse_claude(path):
    row, seen = new_row("claude", path), set()
    for record in read_lines(path):
        when = stamp(record.get("timestamp")) if record.get("timestamp") else None
        if record.get("isSidechain"):
            continue
        touch(row, when)
        row["cwd"] = row["cwd"] or record.get("cwd")
        kind, message = record.get("type"), record.get("message") or {}
        if kind == "user" and not record.get("isMeta"):
            if str(record.get("entrypoint") or "").startswith("sdk") and row["user_prompts"] == 0:
                row["automated"] = True
            content = message.get("content")
            if isinstance(content, list):
                if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                    continue
                content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
            text = clean(content) if isinstance(content, str) else ""
            if text:
                add_prompt(row, when, text)
        elif kind == "assistant":
            key = message.get("id") or id(record)
            if key not in seen:
                seen.add(key)
                row["assistant_turns"] += 1
            row["_last_assistant"] = when
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    row["tool_calls"] += 1
                    row["subagents"] += block.get("name") in ("Agent", "Task")
    return row


def parse_codex(path):
    row = new_row("codex", path)
    for record in read_lines(path):
        when = stamp(record.get("timestamp")) if record.get("timestamp") else None
        touch(row, when)
        payload = record.get("payload") or {}
        if record.get("type") == "session_meta":
            row["session"] = payload.get("id") or row["session"]
            row["cwd"] = payload.get("cwd")
            row["automated"] = payload.get("source") not in ("cli", "vscode")
        if record.get("type") != "response_item":
            continue
        kind, role = payload.get("type"), payload.get("role")
        if kind == "message" and role == "user":
            text = clean("\n".join(b.get("text", "") for b in payload.get("content") or [] if isinstance(b, dict)))
            if text:
                add_prompt(row, when, text)
        elif kind == "message" and role == "assistant":
            row["assistant_turns"] += 1
            row["_last_assistant"] = when
        elif kind in ("function_call", "custom_tool_call"):
            row["tool_calls"] += 1
            row["subagents"] += payload.get("name") == "spawn_agent"
    return row


def read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def transcripts(root, since):
    if not root.is_dir():
        return []
    cutoff = since.timestamp()
    return [p for p in root.rglob("*.jsonl") if "subagents" not in p.parts and p.stat().st_mtime >= cutoff]


def mark_corrections(row, minutes):
    for prompt in row["_prompts"]:
        after = prompt["after"]
        quick = after and prompt["ts"] and (prompt["ts"] - after).total_seconds() <= minutes * 60
        head = prompt["text"][:300]
        prompt["correction"] = bool(quick and (CORRECTION.match(head) or REASK.search(head)))
    row["corrections"] = sum(p["correction"] for p in row["_prompts"])


def mark_repeats(rows, threshold):
    """Inverted index on uncommon words, then exact Jaccard on candidates."""
    items = []
    for i, row in enumerate(rows):
        for prompt in row["_prompts"]:
            words = WORD.findall(prompt["text"].lower())
            prompt["repeat"] = False
            if 5 <= len(words) <= 80:
                items.append((row["start"], i, prompt, {w for w in words if len(w) > 2}))
    items.sort(key=lambda item: item[0] or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    frequency = collections.Counter(w for item in items for w in item[3])
    common = max(5, len(items) // 20)
    index = collections.defaultdict(list)
    for n, (_, owner, prompt, words) in enumerate(items):
        overlap = collections.Counter(m for w in words if frequency[w] <= common for m in index[w])
        for m, shared in overlap.items():
            other = items[m]
            if other[1] != owner and shared / len(words | other[3]) >= threshold:
                prompt["repeat"] = True
                break
        for w in words:
            if frequency[w] <= common:
                index[w].append(n)
    for row in rows:
        row["repeats"] = sum(p["repeat"] for p in row["_prompts"])


def finish(row, show_text):
    prompts = row.pop("_prompts")
    row.pop("_last_assistant")
    if row["start"] and row["end"]:
        row["wall_minutes"] = round((row["end"] - row["start"]).total_seconds() / 60, 1)
    row["start"] = row["start"].isoformat() if row["start"] else None
    row["end"] = row["end"].isoformat() if row["end"] else None
    if show_text:
        row["flagged_text"] = [{"correction": p["correction"], "repeat": p["repeat"], "text": p["text"][:300]}
                               for p in prompts if p["correction"] or p["repeat"]]
    return row


def collect(claude_dir, codex_dir, since, until, args):
    rows = [parse_claude(p) for p in transcripts(claude_dir, since)]
    rows += [parse_codex(p) for p in transcripts(codex_dir, since)]
    rows = [r for r in rows if r["start"] and since <= r["start"] < until and r["user_prompts"]]
    if not args.include_automated:
        rows = [r for r in rows if not r["automated"]]
    for row in rows:
        mark_corrections(row, args.correction_minutes)
    mark_repeats(rows, args.repeat_threshold)
    return [finish(r, args.show_text) for r in rows]


def write(rows, out):
    keep = {}
    if out.is_file():
        for line in out.read_text().splitlines():
            try:
                old = json.loads(line)
                keep[(old["tool"], old.get("file"), old["session"])] = old
            except (ValueError, KeyError):
                continue
    for row in rows:
        keep[(row["tool"], row["file"], row["session"])] = row
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix(".tmp")
    temp.write_text("".join(json.dumps(r) + "\n" for r in sorted(keep.values(), key=lambda r: r["start"] or "")))
    temp.replace(out)
    return len(keep)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", help="YYYY-MM-DD, local time (default: --days ago)")
    parser.add_argument("--until", help="YYYY-MM-DD, exclusive (default: tomorrow)")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--claude-dir", type=Path, default=CLAUDE_DIR, help="env HARNESS_CLAUDE_DIR")
    parser.add_argument("--codex-dir", type=Path, default=CODEX_DIR, help="env HARNESS_CODEX_DIR")
    parser.add_argument("--out", type=Path, default=HOME / "data" / "metrics.jsonl")
    parser.add_argument("--correction-minutes", type=float, default=10)
    parser.add_argument("--repeat-threshold", type=float, default=0.7)
    parser.add_argument("--include-automated", action="store_true", help="keep SDK and exec sessions")
    parser.add_argument("--show-text", action="store_true", help="include flagged prompt text in the output")
    args = parser.parse_args(argv)
    zone = dt.datetime.now().astimezone().tzinfo
    today = dt.datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    since = dt.datetime.fromisoformat(args.since).replace(tzinfo=zone) if args.since else today - dt.timedelta(days=args.days)
    until = dt.datetime.fromisoformat(args.until).replace(tzinfo=zone) if args.until else today + dt.timedelta(days=1)
    rows = collect(args.claude_dir, args.codex_dir, since, until, args)
    total = write(rows, args.out)
    counts = {k: sum(r[k] for r in rows) for k in ("user_prompts", "corrections", "repeats", "subagents")}
    print(f"{len(rows)} sessions in range, {total} rows in {args.out}; " + ", ".join(f"{k}={v}" for k, v in counts.items()),
          file=sys.stderr)


if __name__ == "__main__":
    main()
