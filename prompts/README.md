# Workspace harness prompts

These files are operating rules for Claude Code and Codex agents that work in a large, long-lived workspace. They keep the workspace structured, keep the agents' own instructions in order, and turn each correction into a recorded rule. The agent roles that apply these rules are in [agents/](../agents/README.md). Rules that apply only to Library itself are in the repository's root `AGENTS.md`.

## Files

| File | What it governs |
| --- | --- |
| [orchestrator.md](orchestrator.md) | How the main session turns a request into a ledger, splits it, dispatches by model tier, verifies and reports |
| [self-evolution.md](self-evolution.md) | How a correction becomes a rule, the enforcement ladder, and the four maintenance cadences |
| [measurement.md](measurement.md) | The scoreboard: each metric, how to compute it from session logs, and how to report it |
| [workspace-structure.md](workspace-structure.md) | Where a new file goes, naming, one canonical version, resources tables, what is never deleted without asking |
| [harness-maintenance.md](harness-maintenance.md) | How to edit skills, memory and rule files in order: find the right home, merge, dedupe, size limits, changelog, pruning |
| [daily-maintenance.md](daily-maintenance.md) | The daily project rescan, codebase hygiene pass and stale-file sweep |
| [verification.md](verification.md) | Checking results yourself and reporting what is verified separately from what is claimed |
| [safety.md](safety.md) | Drafts only for outward actions, confirmation before destructive or public actions, running sessions, user edits |

## Terms

- **Workspace**: the folder tree you and your agents work in, with its projects, tools and data.
- **Harness**: everything that shapes agent behavior: rule files, skills, memory, agent definitions, these prompts, router patterns, hooks and the logs they keep.
- **Rule file**: the instruction file an agent loads every session: `CLAUDE.md` for Claude Code, `AGENTS.md` for Codex.
- **Harness directory**: one folder you choose for harness scripts, data and reports, for example `~/.claude/harness/`. The files below call it `<harness>`.
- **Task**, **correction**, **repeat**: defined in [measurement.md](measurement.md).

## Install

Load four files in every session: `orchestrator.md`, `workspace-structure.md`, `verification.md` and `safety.md` (about 17 KB together). Load the other four on demand; the steward, librarian and learner agents read them when they run. The scripts that the measurement and self-evolution prompts rely on are in `scripts/harness/` in this repository, and its README names each script.

**Claude Code.** Copy this folder to `~/.claude/harness/prompts/`, then:

1. Import the always-on files from `~/.claude/CLAUDE.md` (every project) or a project's `CLAUDE.md`, one `@` line each. Claude Code expands the imports at session start, and subagents load the same files. In a project `CLAUDE.md`, an import from outside the project asks for approval once.

   ```text
   @~/.claude/harness/prompts/orchestrator.md
   @~/.claude/harness/prompts/workspace-structure.md
   @~/.claude/harness/prompts/verification.md
   @~/.claude/harness/prompts/safety.md
   ```
2. Add one line under the imports so agents can find the on-demand files: `prompts/<file>.md in agent and prompt files means ~/.claude/harness/prompts/<file>.md.`
3. To load an on-demand file as a skill, create `~/.claude/skills/<name>/SKILL.md` with `name` and `description` frontmatter and the file's text as the body. The description decides when it loads, so name the situations: "Use for the daily rescan, hygiene pass or stale-file sweep."

**Codex.** Codex reads `~/.codex/AGENTS.md` in every project, then each `AGENTS.md` from the project root down to the working folder, up to 32 KiB combined by default. `AGENTS.md` has no import syntax. Paste the always-on files into `~/.codex/AGENTS.md`, or add a line that tells the agent to read them from `~/.claude/harness/prompts/` at the start of each session. Keep one canonical copy of each file and point both tools at it, so the two never drift. For any other agent, paste the always-on files into the system prompt.

## Adapt before use

Replace the examples with your own folder names, add your hard rules to the rule file, and set the model tiers in one config file (see [self-evolution.md](self-evolution.md), model currency). Keep your personal rules in your private rule file and memory; these shared files stay general.
