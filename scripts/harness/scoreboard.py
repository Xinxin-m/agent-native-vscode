#!/usr/bin/env python3
"""Turn session_metrics.py output into a weekly Markdown scoreboard.

Reads <HARNESS_HOME>/data/metrics.jsonl and writes <HARNESS_HOME>/reports/scoreboard.md
(or --out; use --out - for stdout). Weeks start on Monday, local time. Each rate is
printed with the counts behind it and an arrow against the previous week.
"""
import argparse
import collections
import datetime as dt
import json
import os
import sys
from pathlib import Path

HOME = Path(os.environ.get("HARNESS_HOME", Path.home() / ".library-harness"))

DEFINITIONS = [
    ("Prompts/session", "lower", "human prompts divided by sessions; fewer prompts for the same work means less steering."),
    ("Corrections/session", "lower", "prompts flagged by the correction heuristic (negation or re-ask soon after a reply), per session."),
    ("Repeated", "lower", "prompts that nearly duplicate an instruction from an earlier session (word-set similarity)."),
    ("Delegation", "higher", "share of sessions with at least --min-prompts prompts that dispatched one or more subagents."),
]


def week_of(stamp):
    day = dt.datetime.fromisoformat(stamp).astimezone().date()
    return day - dt.timedelta(days=day.weekday())


def load(path, include_automated=False):
    rows = []
    for line in path.read_text().splitlines() if path.is_file() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("start") and (include_automated or not row.get("automated")):
            rows.append(row)
    return rows


def summarize(rows, min_prompts):
    weeks = collections.defaultdict(list)
    for row in rows:
        weeks[week_of(row["start"])].append(row)
    table = {}
    for week, group in weeks.items():
        sessions = len(group)
        prompts = sum(r.get("user_prompts", 0) for r in group)
        corrections = sum(r.get("corrections", 0) for r in group)
        repeats = sum(r.get("repeats", 0) for r in group)
        long = [r for r in group if r.get("user_prompts", 0) >= min_prompts]
        delegated = sum(1 for r in long if r.get("subagents", 0) > 0)
        table[week] = {
            "sessions": sessions, "prompts": prompts, "corrections": corrections, "repeats": repeats,
            "long": len(long), "delegated": delegated,
            "Prompts/session": prompts / sessions,
            "Corrections/session": corrections / sessions,
            "Repeated": float(repeats),
            "Delegation": delegated / len(long) if long else None,
        }
    return dict(sorted(table.items()))


def arrow(now, before, better):
    if now is None or before is None:
        return ""
    if abs(now - before) <= 0.05 * max(abs(before), 1e-9) or now == before:
        return " →"
    up = now > before
    good = up == (better == "higher")
    return (" ↑" if up else " ↓") + (" better" if good else " worse")


def render(table, min_prompts, weeks):
    keys = list(table)[-weeks:]
    lines = ["# Harness scoreboard", "",
             "These numbers are proxies computed from transcripts with text heuristics. Read the trend over several",
             "weeks and the counts beside each rate; a single week with few sessions says little.", "",
             "| Week of | Sessions | Prompts/session | Corrections/session | Repeated | Delegation |",
             "| --- | --- | --- | --- | --- | --- |"]
    earlier = list(table)[: len(table) - len(keys)]
    previous = table[earlier[-1]] if earlier else None
    for week in keys:
        row = table[week]
        cell = {}
        for name, better, _ in DEFINITIONS:
            before = previous[name] if previous else None
            mark = arrow(row[name], before, better)
            if name == "Delegation":
                cell[name] = "n/a" if row[name] is None else f"{row[name]:.0%} ({row['delegated']}/{row['long']}){mark}"
            elif name == "Repeated":
                cell[name] = f"{row['repeats']}{mark}"
            else:
                count = row["prompts"] if name == "Prompts/session" else row["corrections"]
                cell[name] = f"{row[name]:.2f} ({count}/{row['sessions']}){mark}"
        lines.append(f"| {week.isoformat()} | {row['sessions']} | " + " | ".join(cell[n] for n, _, _ in DEFINITIONS) + " |")
        previous = row
    lines += ["", "## What each column means", ""]
    for name, better, text in DEFINITIONS:
        lines.append(f"- **{name}** ({better} is better): {text.replace('--min-prompts', str(min_prompts))}")
    lines += ["", "Arrows compare with the previous week; a change within 5% shows →."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--metrics", type=Path, default=HOME / "data" / "metrics.jsonl")
    parser.add_argument("--out", default=str(HOME / "reports" / "scoreboard.md"), help="file path, or - for stdout")
    parser.add_argument("--weeks", type=int, default=8)
    parser.add_argument("--min-prompts", type=int, default=5, help="session size counted for the delegation rate")
    parser.add_argument("--include-automated", action="store_true")
    args = parser.parse_args(argv)
    table = summarize(load(args.metrics, args.include_automated), args.min_prompts)
    text = render(table, args.min_prompts, args.weeks) if table else "# Harness scoreboard\n\nNo sessions in the metrics file.\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"{len(table)} weeks -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
