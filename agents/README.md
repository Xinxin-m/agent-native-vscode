# Agent roster

General agent roles for a large engineering or research workspace. Each file is a Claude Code subagent definition (YAML frontmatter, then the prompt) and also reads as a Codex role prompt. The rules they apply are in [prompts/](../prompts/README.md). The orchestrator is the main session itself, so its rules live in [prompts/orchestrator.md](../prompts/orchestrator.md).

## Roster

| Agent | Tier | Use it for |
| --- | --- | --- |
| [engineer](engineer.md) | strongest | building, fixing and refactoring code, and reviewing a delivered app against its requirements |
| [reviewer](reviewer.md) | strongest | refuting a plan, design, change or draft before the user sees it (read-only) |
| [research-auditor](research-auditor.md) | strongest | checking an analysis: equation, units, sample, groups, claims against output (read-only) |
| [researcher](researcher.md) | strongest | designing and running an analysis, experiment or source review for the user's question |
| [ui-designer](ui-designer.md) | strongest | constraints, variants and subtraction for interfaces, pages and decks |
| [figure-designer](figure-designer.md) | standard | charts, plots, KPI tiles and table exhibits |
| [writer](writer.md) | standard | reports, docs, READMEs, plans, meeting notes and message drafts |
| [editor](editor.md) | strongest | the final prose pass on anything that ships |
| [task-tracker](task-tracker.md) | fast | the request ledger at the start and the dropped-request check at the end (read-only) |
| [workspace-steward](workspace-steward.md) | standard | daily hygiene, stale-file sweep, file placement and resources tables |
| [librarian](librarian.md) | standard | daily project rescan and classification proposals |
| [learner](learner.md) | standard, strongest weekly | turning each session's corrections into harness rules; the weekly consolidation |
| [ops-runner](ops-runner.md) | standard | fetches, conversions, scheduling, forms and drafts; never sends |
| [release-manager](release-manager.md) | standard | building and checking a private-data-free release of this repository |

Tiers: **fast** for mechanical work, **standard** for rule-following work, **strongest** for judgment. Map each tier to a current model in one config file; the files never name a model id, because ids go stale.

## Install

Install the prompts first ([prompts/README.md](../prompts/README.md)). Role bodies name them as `prompts/<file>.md`, and the line you add to your rule file there tells the agent where that folder is.

**Claude Code.** Copy the role files (not this README) into `~/.claude/agents/` for every project, or into `.claude/agents/` for one repository; a project file wins over a user file with the same name. Each file has `name` and `description` frontmatter, optional `tools` and `model`, and a body that becomes the agent's system prompt. Claude Code delegates to an agent when a task matches its `description`; to call one yourself, say "use the reviewer agent" or type `@agent-reviewer`. To pin a model, add a `model:` line with the alias you mapped to the agent's tier.

**Codex.** Codex loads its instructions from `AGENTS.md` files, which have no import syntax. List the roles in `~/.codex/AGENTS.md` or the repository's `AGENTS.md`, one line each with the path to the role file, and tell the agent to read that file before it acts in the role or briefs a worker for it. You can also paste a role's body into a worker's brief.

## Add or change an agent

- Add a role only when the work recurs and needs rules or a stance that no existing role has. Otherwise extend the closest role.
- One file per role, named after the role in lower-case kebab case. The `description` says when to use the agent and what it returns; it decides when the agent is picked.
- Write each body with the same sections: when to use, what it owns, rules, returns, model tier. Keep it under 120 lines.
- Give `tools:` only to restrict a role, as for read-only reviewers.
- Rules that apply to every agent belong in [prompts/](../prompts/README.md). Refer to them by path (`prompts/<file>.md`) instead of copying them; a relative link breaks once the role file is copied.
- Change a role in place and log the change in the harness changelog ([prompts/harness-maintenance.md](../prompts/harness-maintenance.md)). The learner retires roles that are never dispatched and rewrites roles with repeated non-clean outcomes.
- Keep personal or domain-specific roles in your private agent folder, outside this shared set.
