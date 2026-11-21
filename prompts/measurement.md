# Measuring progress

The harness is improving when a finished task costs the user fewer prompts and fewer corrections, and when no instruction has to be given twice. Compute the numbers weekly from session logs and report the trend.

## Definitions

- **Session:** one transcript file.
- **Human prompt:** a message the user typed. Exclude tool results, hook output, injected instruction blocks, slash-command wrappers, and sessions started by automation (scheduled jobs, labeling calls, hooks).
- **Task:** one deliverable or goal. A session can hold several tasks, and a task can span sessions.
- **Finished task:** a task whose outcome is done. Count abandoned and ongoing tasks separately.
- **Correction:** a human prompt that fixes output, re-asks, or restates a preference.
- **Repeat:** a correction of a rule that was recorded in a rule file, skill or memory before the session started.
- **Dropped request:** a ledger item the task tracker marked missing at the end of a turn.

## Metrics

| Metric | Computation | Better |
| --- | --- | --- |
| Prompts per finished task | human prompts spent on finished tasks / finished tasks | lower |
| Corrections per task | corrections / tasks | lower |
| Repeats | repeats per week | lower; the target is 0 |
| Dropped requests | missing items / turns the tracker checked | lower |
| Skill compliance | router skill injections followed by that skill in the same session / injections. Without a router: tasks where the right skill ran / tasks that needed one | higher |
| Delegation rate | sessions with 5+ human prompts that dispatched a subagent / sessions with 5+ human prompts | higher, while prompts per task also falls |
| Correction signal rate | regex-detected corrections per 100 human prompts | lower |
| Workspace structure | stray root files, version-copy groups, projects without a resources table, unclassified conversations | lower |

The correction signal rate needs no model, so it gives a baseline for weeks before the learner existed. The structure counts come from the daily maintenance report.

## Where the numbers come from

Raw transcripts:

- **Claude Code:** `~/.claude/projects/<encoded-folder>/<session-id>.jsonl`, one JSON record per line. A human prompt is a record with `"type": "user"` whose content is text; a user record whose content is a `tool_result` is tool output.
- **Codex:** `~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl`. A user message is a `response_item` record whose payload has type `message` and role `user`. Drop injected instruction blocks, which begin with headings such as `# AGENTS.md instructions`.

Library's indexer (`app/indexer.py`) already parses both formats into clean transcripts; reuse it.

Pipeline, with files in `<harness>/data/` and `<harness>/reports/`. The scripts are in `scripts/harness/`, and its README names them:

1. The sweep script (no model) runs from the session-end hook and nightly. It appends one row per session to `metrics.jsonl`: human prompts, subagents dispatched, skills invoked, regex correction signals.
2. The learner agent reads each session and appends task and correction rows:
   - `tasks.jsonl`: `{session, date, task, category, prompts, corrections, repeats, outcome, root_cause}`
   - `corrections.jsonl`: `{date, session, quote, rule, scope, repeat_of, frustrated}`, where `frustrated` is true when the prompt shows irritation (capitals, "again", "how many times")
   - `skill_outcomes.jsonl`: `{date, session, kind, name, outcome, note}`
3. After the task tracker's end-of-turn check, the orchestrator appends `{ts, session, missing}` to `task_tracker.jsonl`.
4. The router hook logs each rule it injects to `router_log.jsonl`.
5. The scoreboard script groups all rows by week, with weeks starting Monday, and writes `reports/scoreboard.md`: one row per week, one column per metric, with the counts behind each rate.

## Reading the scoreboard

- Compare each week with the previous week and with the 4-week average. Small samples swing, so print the count next to each rate.
- Read rates. A busy week raises every total.
- A metric that worsens two weeks running needs a cause found in the data (which tasks, which corrections) and a fix, recorded in the weekly report.
- Delegation is a means. If the delegation rate rises while prompts per task also rise, workers are producing output the user has to fix.
- A fall in repeats after a rule climbs the enforcement ladder is the evidence that the climb worked. Note it in the changelog.

## Reporting

The weekly pass writes `reports/weekly-<YYYY-MM-DD>.md`, under 400 lines:

1. the scoreboard table;
2. the metrics that moved most, each with its cause;
3. every harness change made that week, one line each;
4. items listed but left alone, with the reason;
5. questions for the user.

Tell the user only the headline: which metrics moved and why, in two or three lines.
