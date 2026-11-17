# Workspace structure and output placement

Every file has one place. The agent that creates a file chooses that place before writing it.

## Layout

- **Same kind, same parent.** All projects sit under one projects folder, all shared tools under one tools folder, all docs of a project under its `docs/`. A new folder goes beside its siblings of the same kind.
- **No stray files.** A root holds only the README, license, entry-point launchers and config files that tools require there. Everything else goes into a named folder.
- **Keep folders flat.** Add a subfolder when it will hold several files of one kind or separate independent work. One file does not justify a folder.
- **One canonical version** of every document, prompt, script and skill (see below).

## Where a new file goes

Ask in order and stop at the first yes.

1. **Does a file for this already exist?** Search by topic and name. Edit it in place.
2. **Is it scratch?** A probe, a one-off log or a temporary render goes in the system temp folder or the project's ignored `tmp/`. Delete it when the task ends.
3. **Does it belong to one project?** Put it in that project, in the folder the project already uses for its kind:

   | Kind | Folder |
   | --- | --- |
   | source code | the project's existing code folder, else `src/` |
   | scripts and tooling | `scripts/` |
   | plans, requirements, notes, guides | `docs/` |
   | raw and collected data | `data/` |
   | outputs of a run | `results/<run-name>/` or `output/` |
   | figures and exhibits | `figures/` |
   | deliverables that leave the machine | the project's deliverables folder |

   Create a folder only when none exists for that kind, and use the conventional name.
4. **Does it serve several projects or the workspace itself?** Put it in the shared folder for its kind: tools, templates or skills.
5. **Is it a harness file** (rule, skill, memory, agent, harness data)? Follow [harness-maintenance.md](harness-maintenance.md).
6. **Nothing fits?** Do not create a new top-level folder on your own. Put the file in the closest project and propose a home in your report.

After placing a file, add it to the project's resources table.

## Naming

- Lower-case kebab case: `<project>-<what-it-is>.<ext>`. Add the date as `YYYY-MM-DD` to dated documents and to anything sent or uploaded: `sales-report-q3-summary-2026-10-01.pdf`.
- Never give a deliverable a generic name such as `report.pdf`, `deck.html`, `output.csv` or `final.md`. The name is the only context a downloaded or forwarded file carries. Conventional names inside a package (`README.md`, a site's `index.html`) are fine.
- Change the build script's output path as well, or the next run recreates the generic name.

## One canonical version

- Edit in place. Never create `-v2`, `-new`, `-final`, `-old`, `copy`, `(1)` or date-suffixed copies for an ordinary revision. Version history belongs in git or the editor's history.
- For a radical rewrite the user should validate first, write the new file and, in the same message, propose deleting the old one and renaming the new one to the canonical name. Keep at most the current and the previous version.
- When copies already exist, inventory all of them, including copies inside skills. Merge the best of each into one unversioned file, repoint every link and build input, then remove the rest. Delete superseded prompts and code; move superseded documents to the trash.
- A skill that both Claude Code and Codex use has one copy; link each tool's skill folder to it.
- A report rebuilt from the same source overwrites its local copy under the same name, and any open view of it is reloaded.

## Resources table per project

Each project's README starts with a Resources table. Read it before working on the project. Add a row in the same session a file, link, dataset or external folder appears.

| Resource | Where | Notes |
| --- | --- | --- |
| Raw orders | `data/orders/` | one CSV per day, one row per order |
| Q3 summary | `deliverables/sales-report-q3-summary-2026-10-01.pdf` | sent to the client on 2026-10-01 |
| Shared drive folder | link | source spreadsheets |

## Data documentation

Each dataset gets an entry with: path and file-name pattern; row grain (what one row is); coverage (dates, file count, size); the code that built it; a column table with types and meanings; known caveats. Verify each entry against the filesystem and record the date. When you find a new fact about the data, record it and correct the older claim in place, noting where the old claim still holds. Keep an index of data docs with a status for each: current, partly superseded or historical.

## Never deleted without asking

- Raw or collected datasets, finished run outputs and results, figures someone may still use, backups.
- Any path with a folder segment named `data`, `datasets`, `raw`, `results`, `output`, `outputs`, `figures`, `backups` or `logs`. Cleanup scripts skip these segments.

Without version control these are unrecoverable. Code, prompts, caches and scratch can be removed under [daily-maintenance.md](daily-maintenance.md).
