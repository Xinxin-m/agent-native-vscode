# Daily maintenance

Three routines run at least once a day, at a quiet hour from a scheduler or in the first session of the day. Each one lists what it checks, what it may change on its own, and what it may only propose. Each writes a dated section in `<harness>/reports/maintenance.md` and never touches running sessions or services ([safety.md](safety.md)).

## 1. Project rescan and classification

Owner: the librarian agent.

Checks:

- Import new and changed sessions with the deterministic refresh, which makes no model calls.
- Read the classification review the refresh writes. It lists recent and changed conversations, conflicts with purpose rules, groupings based only on the working folder, duplicate project names, empty project shells, parents with one child, and projects whose conversations are all archived.
- Sample recently classified conversations and check each against its project's purpose. A zero unassigned count does not prove the classification is right.
- Look for drift: a project that grew to cover another, a topic with several conversations and no project, a conversation that fits nothing.
- On disk, check that each project folder sits under the right parent and has a README with a resources table.

May do alone: run the parse-only refresh and the review; assign an unassigned conversation when a purpose rule matches it clearly.

Must propose: merges, splits, renames, new projects, archiving a project, moving a conversation that already has an assignment, and any model-backed classification beyond the budget the user configured.

Rules: manual placement always wins. Never run a full reclassification that discards manual assignments. Turn each accepted decision into a purpose rule in the private config so later refreshes keep it. Log every merge and split. Keep the project list flat unless independent workstreams justify nesting. In Library, the root `AGENTS.md` lists the refresh, review and reclassification commands.

## 2. Codebase hygiene

Owner: the workspace-steward agent; the engineer makes approved fixes.

Checks, for each maintained repository:

- Stray files at the root, and files in the wrong folder for their kind.
- Version copies (`-v2`, `-old`, `copy`, `(1)`, date suffixes) beside an original.
- Dead code: modules, scripts and assets referenced nowhere; commented-out blocks; duplicate helpers.
- Build products, caches and installed dependencies that are tracked or would ship.
- Tests and the build pass; linters run where the project has them.
- Docs that contradict the code: commands, paths and flags in the README.
- Hard-coded model ids, personal paths or credentials in shippable files. Run the project's package privacy scan where it has one (in Library: `python3 scripts/package_workspace.py --output <new-folder>`).

May do alone: delete caches (`__pycache__`, `.pytest_cache`, `.DS_Store`); fix a dead link or path in docs; run tests and the privacy scan.

Must propose: deleting or moving source files, refactors, dependency changes, anything in a protected folder ([workspace-structure.md](workspace-structure.md)). A user can allow code moves and deletions in the rule file; then the steward makes them and lists each one.

## 3. Stale-file sweep

Owner: the workspace-steward, which lists candidates by hand (for example with `find`) and decides each item.

Checks across the workspace:

- Scratch older than 7 days, logs older than 30 days outside protected folders, empty folders, broken symlinks.
- Superseded documents: same project, older, referenced nowhere, covered by a newer canonical file.
- Projects with no activity for 60 days: candidates to archive.
- Files created this week that are missing from their project's resources table.

May do alone: remove caches, old scratch, old logs and empty folders outside protected folders; remove broken symlinks; add missing resources-table rows.

Must propose: moving superseded documents to the trash, archiving projects, and anything in a protected folder. When unsure whether a file is superseded, list it and leave it.

## Report

Each routine writes:

1. the structure counts for the scoreboard ([measurement.md](measurement.md)): stray root files, version-copy groups, projects without a resources table, unclassified conversations;
2. what it changed, one line each with the path;
3. proposals awaiting the user, one line each with the command that would apply it.

Show open proposals at the start of the next session. Ask about an unanswered proposal once more after 7 days, then drop it.
