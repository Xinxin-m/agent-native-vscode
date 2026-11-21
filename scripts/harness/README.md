# Harness scripts

These scripts back the rules in [prompts/](../../prompts/README.md) with working tools. They use Python 3.11 or newer and the standard library only. They make no network or model calls.

## Scripts

| Script | What it does |
| --- | --- |
| `session_metrics.py` | Reads Claude Code and Codex transcripts and writes one JSON line per session: prompts, replies, tool calls, subagents, wall time, corrections and repeated instructions. |
| `scoreboard.py` | Turns those lines into a weekly Markdown table with trend arrows. |
| `sweep.py` | Lists stray root files, version copies, empty folders, stale scratch, large files, duplicates, broken Markdown links and scattered folders. |
| `router_hook.py` | A prompt hook that injects the rules whose pattern matches the prompt. `--test` reports how often each pattern fires. |
| `router_rules.example.json` | Five starter rules. Copy it to `$HARNESS_HOME/router_rules.json` and edit. |
| `session_end_hook.py` | A session-end hook that queues the finished session for the learner agent. |
| `install_hooks.py` | Adds or removes the two hooks in a settings file without touching other hooks. |
| `test_harness.py` | Tests on synthetic data in temp folders. |

Every script has `--help`.

## Where output goes

All data lives in one folder, `$HARNESS_HOME`, by default `~/.library-harness/`. Metrics and queues go to `data/`, the scoreboard to `reports/`, settings backups to `backups/`. The sweep writes only where `--md` and `--json` point. Transcript locations can be changed with `--claude-dir` and `--codex-dir` or `HARNESS_CLAUDE_DIR` and `HARNESS_CODEX_DIR`.

## Set up once

```sh
mkdir -p ~/.library-harness
cp scripts/harness/router_rules.example.json ~/.library-harness/router_rules.json
python3 scripts/harness/install_hooks.py --dry-run
python3 scripts/harness/install_hooks.py
```

For Codex, pass `--settings ~/.codex/hooks.json`. Codex reads the same hook structure there, and it runs a new or changed hook only after you review and trust it with `/hooks`.

## Daily

```sh
python3 scripts/harness/session_metrics.py --days 2
python3 scripts/harness/sweep.py ~/work --md ~/.library-harness/reports/sweep.md --json ~/.library-harness/data/sweep.json
```

Read the sweep report, decide each item, and run `sweep.py ~/work --fix` to remove empty folders and OS junk files. Nothing that matches the protect list is touched: `data/`, `datasets/`, `raw/`, `results/`, `output/`, `outputs/`, `figures/`, `backups/`, `logs/`, `notebooks/`, `*.csv`, `*.parquet` and `*.ipynb`. Add patterns with `--protect`.

To gate a commit, add `--max stray_root=0 --max version_copies=0`; the script exits 1 when a count is over its limit.

## Weekly

```sh
python3 scripts/harness/session_metrics.py --days 8
python3 scripts/harness/scoreboard.py
python3 scripts/harness/router_hook.py --test
```

Example schedule line for cron or any scheduler that takes the same five fields:

```text
0 3 * * * cd /path/to/repo && python3 scripts/harness/session_metrics.py --days 2 && python3 scripts/harness/scoreboard.py
```

## Which prompt uses which script

| Prompt or agent | Script |
| --- | --- |
| `measurement.md`, the learner's weekly mode | `session_metrics.py`, `scoreboard.py` |
| `self-evolution.md`, enforcement ladder step 3 | `router_hook.py` and its rules file, `router_hook.py --test` for the 15% check |
| `self-evolution.md`, per-session cadence; the learner | `session_end_hook.py` fills `data/learner_queue.jsonl` |
| `daily-maintenance.md` routines 2 and 3; the workspace-steward | `sweep.py` |
| `workspace-structure.md`, "Never deleted without asking" | the `sweep.py` protect list |
| `harness-maintenance.md`, harness directory | `install_hooks.py`, `$HARNESS_HOME` |

## Limits

The correction and repeat counts are text heuristics in English. They miss polite or indirect corrections and count some habitual requests as repeats, so read them as trends. Transcript formats belong to Claude Code and Codex and can change between releases; when counts drop to zero, check the parser first. Sessions started by the SDK or by `codex exec` are skipped unless you pass `--include-automated`.
