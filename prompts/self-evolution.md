# Self-evolution

The goal: turn each correction into a recorded rule, so that each week a finished task costs the user fewer prompts. Every interaction is evidence. Rule files, skills, memory, agent definitions and the workspace software all change in response to it. [measurement.md](measurement.md) shows whether the changes worked.

## Signals

- **Correction:** the user fixes output, re-asks, or restates a preference.
- **Repeat:** a correction of a rule that was already recorded. A repeat means the place the rule was stored failed to reach the agent.
- **Approval:** output the user accepted unchanged shows what to keep doing.
- **Recurring job:** the same kind of task three or more times with no skill for it.
- **Software friction:** a control the user had to hunt for, a manual step they repeat, a request about how the app behaves.

Record a durable rule after one explicit statement or two consistent signals. Instructions that only fit the current task stay with the task.

## How a correction becomes a rule

1. Fix the artifact.
2. Find the file that owns the rule ([harness-maintenance.md](harness-maintenance.md), "Before any edit").
3. Search the rule files, skills and memory for the same rule. If it exists, the correction is a repeat: climb the ladder below. If it does not, write it into its owning file with the reason and the example that failed.
4. Add a check where one is possible: a test, a grep, a hook.
5. Log the change in the changelog and tell the user in one line which file changed and what it now says, so the user can correct the rule itself next time.

## Enforcement ladder

Each repeat of a recorded rule moves it one step up.

1. **Memory file.** Loaded when relevant.
2. **Rule file, hard-rules section.** Loaded in every session.
3. **Router rule.** A prompt hook (the router hook in `scripts/harness/`) matches patterns in the user's message and injects the rule at the moment it applies. Anchor patterns on words the user actually types. A pattern that fires on more than 15% of last week's prompts is too broad.
4. **Hook.** A deterministic script that warns or blocks, for anything mechanically checkable. The agent writes it for the rule at hand. Example: a write hook that flags `sales-report-v2.md` created beside `sales-report.md`.

Prune downward as well. A router rule that has not matched in 8 weeks can go once the rule lives in the hard-rules section.

## Cadences

| Cadence | Who | What |
| --- | --- | --- |
| Every use | the working agent | Follow injected rules. Record a durable lesson in its owning file the same hour. |
| Every session | learner agent, standard tier, after the session ends | Split the session into tasks, log corrections and repeats, grade each skill and agent used, fix causes, write the changelog. |
| Nightly | the sweep and scoreboard scripts in `scripts/harness/`, no model | Session metrics and the scoreboard refresh. Cleanup and listings ([daily-maintenance.md](daily-maintenance.md)) are done by the steward. |
| Weekly | learner in weekly mode, strongest tier | Consolidate memory and skills, prune, build proposed skills and hooks, rebuild router patterns, check model currency, review the changelog, write the weekly report. |

The session-end hook in `scripts/harness/` starts the per-session pass. If it has not run for 30 hours, the next session says so and fixes it.

## Missed skills and agents

For each task, decide which skill or agent should have run and whether it did. Fix the cause:

- The skill description lacks the user's words: add them to its trigger phrases.
- The router did not match: add a pattern and test that it compiles.
- The router matched and the agent ignored it: add the rule to the hard-rules section.
- Two skills competed: sharpen both descriptions ("Use for X. Not for Y; use Z.").
- Work was done inline that should have been delegated: log it; it lowers the delegation rate.
- No skill exists for a job seen three or more times: write a proposal with its trigger, steps and inputs. The weekly pass builds it.

## Grading

After each session, give every skill and agent used one outcome: clean, corrected (name what the user fixed), failed, or misrouted. Fix the file at once when the lesson is durable. Three non-clean outcomes in 14 days put the skill or agent on the weekly rewrite list.

## The software evolves too

A correction about the workspace app is a requirement. Record it in the project's requirements file with an ID and an observable acceptance check, then implement it or queue it. Apply a UI change to every client of the same product. A later review checks that the acceptance check would catch a regression.

## Model currency

Keep one config file that maps each tier to the current model id for each provider. Agents and scripts read the tier and never hard-code an id. When a provider ships a newer model of the same strength, update the map; prefer the newer family. The weekly pass checks this and replaces stale ids anywhere in the harness.

## When to ask the user

The learner decides on its own except in three cases: two explicit statements by the user conflict and recency does not settle it; a change would delete data or results; a change would affect an outward action. It writes one line per question to `<harness>/data/questions.md`, and the next session shows them.
