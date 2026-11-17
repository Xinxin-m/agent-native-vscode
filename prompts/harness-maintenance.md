# Maintaining the harness

The harness is the set of files that shapes agent behavior: rule files (`CLAUDE.md`, `AGENTS.md`), skills, memory, agent definitions, these prompts, router patterns, hooks, and their data. Every edit to it is a placement decision. A line added in the wrong place, or added beside a line that already says it, makes the harness slower to read and easier to ignore.

## Before any edit

1. **Read the current file.** Another agent may have changed it. Use targeted edits; never overwrite a shared file wholesale.
2. **Search everything.** Grep the topic's key words across rule files, skills, memory and agent files. The rule may exist already, contradict another, or belong elsewhere.
3. **Choose the single most upstream home:**

   | The rule governs | Home |
   | --- | --- |
   | one skill's output | that skill |
   | one project's behavior | that project's requirements file or README |
   | one role's behavior | that agent definition |
   | how the user works, in general | memory |
   | every session, and it was broken before | the rule file's hard-rules section |
   | facts about a codebase | that codebase's docs |

   Other files link to the home; they never copy the text.

## How to insert

- Find the section the rule belongs to. Add a new section only if it will hold more than one rule; otherwise reconsider the home.
- Merge or replace before you append. If an existing line covers part of the rule, rewrite that line. If the new rule supersedes an old one, delete the old one in the same edit.
- Resolve contradictions in favor of the user's most recent explicit statement and edit the losing text. If recency does not settle it, ask.
- Write each rule as an imperative with its reason in a few words. Add the example that failed when the rule is ambiguous without it. Keep the user's own words where they are precise.
- Order rules within a section by how often they are needed.
- Fold dated correction entries into the rules they changed. A growing dated log inside a skill is stacking.

## Memory

- One file per topic. Frontmatter: `name`, a one-line `description`, `type` (user, feedback, project, reference). Body: the rule, then **Why:** and **How to apply:**.
- An index file lists every memory in one line under a fixed set of headings. Update the index in the same edit as the file.
- Before creating a file, find the closest existing one and merge if they share a topic.
- Paths and IDs go in reference files, preferences in feedback files, project status in project files.

## Skills

- The `description` decides when a skill loads. State what it does, when to use it in the words the user types, and "Not for X; use Y" when a neighbor competes.
- The body holds steps and rules. Move long reference material into `references/` files that the body links.
- Keep project-specific rules in the project and transferable rules in the skill.

## Size limits

| File | Limit |
| --- | --- |
| rule file | 300 lines |
| memory index | 150 lines |
| memory folder | 160 files |
| skill body | 500 lines; move detail to `references/` |
| prompt or agent file | 120 lines |

When a write would cross a limit, merge the closest entries first. Never lose a distinct rule in a merge.

## Changelog

Append one line per change to `<harness>/reports/changelog.md`:

```text
2026-10-01 | skills/sales-report/SKILL.md | added: totals exclude refunds | session 4f2a
```

The weekly pass reads it and reverts any change a later correction proved wrong.

## Pruning, weekly

- Skills unused for 60 days and agents never dispatched: merge into a sibling, sharpen the trigger if they should have fired, or move to an archive folder with a one-line reason.
- Skills or agents with three non-clean outcomes in 14 days: rewrite the failing part from the corrections behind them.
- Memories proven wrong, or pointing only at paths that no longer exist: delete them.
- Router patterns that never match: remove. Patterns that fire too often: narrow.
- Dead links and paths: fix.

## Harness directory

```text
<harness>/
  prompts/    these files
  bin/        the scripts from scripts/harness/: sweep, scoreboard, router and session-end hooks
  data/       metrics, tasks, corrections, skill outcomes, router patterns, proposals, questions.md
  reports/    scoreboard.md, changelog.md, weekly and maintenance reports
  logs/       script logs, kept 30 days
```

Agent definitions live where the agent tool reads them (`~/.claude/agents/` for Claude Code). Back up memory weekly and keep the last four backups.
