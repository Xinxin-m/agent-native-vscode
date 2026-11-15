# Install Library Workspace for your user

Give your agent this instruction:

> Read the repository README.md and this guide, then set up Workspace on my computer. Find my Claude and Codex installations, index my existing chats without paid model calls, help me sign into each account, choose my working folders, and open the finished workspace. Preserve my existing settings and data. Tell me what still needs my input.

Use this guide when an agent installs this project on another person's computer. Each installation keeps that person's transcripts, accounts, projects, and drafts locally. Start with a clean source checkout or the prepared share archive. Preserve any existing `app/data/` directory and provider configuration.

## Requirements

- Python 3.11 or newer for the server and native configuration reader.
- A browser and a trusted local user account. The server binds to `127.0.0.1`; it is intended for one person's computer. Keep it off public interfaces and public reverse proxies.
- Claude Code, Codex, or both, installed and signed in as that user. Viewing existing transcripts and launching agents are separate capabilities.
- Node.js and npm only when rebuilding the frontend. The share archive includes the built client in `web/workbench/`. For source builds, use a Node version supported by the checked-in frontend dependencies and preserve `workbench/package-lock.json`.

For backend commands below, work from `app/`, the directory containing `server.py`, `pipeline.py`, and the runtime `run.sh`. The repository root launchers also work from any current directory. Check the interpreter and available provider CLIs without sending a prompt:

```sh
python3 --version
command -v claude
command -v codex
```

## First local launch

Create configuration only when it does not already exist:

```sh
mkdir -p data
cp -n data/config.example.json data/config.json
python3 pipeline.py --parse-only
./run.sh
```

Open `http://127.0.0.1:8744/workbench`. The root page opens the bundled Workspace. A different port can be set in `data/config.json`.

A machine without histories should start with an empty library; never copy somebody else's transcripts to make the page look populated. Provider login can be skipped until the user wants to send messages. Install missing providers from the official [Claude Code setup guide](https://code.claude.com/docs/en/setup) or [Codex CLI guide](https://github.com/openai/codex). Let the user finish authentication privately, then verify identity without sending a model prompt.

The parse-only command reads the user's native transcript history and updates the local index without model calls. Use parse-only refreshes for installation and QA. Model-generated titles and project classification require the user's explicit opt-in; they can consume the user's provider allowance. `enrich_limit: 0` means unlimited labeling, so it must never be used as a way to disable labeling.

Review the default transcript roots in `data/config.json`. Standard locations are `~/.claude/projects`, `~/.codex/sessions`, and `~/.codex/session_index.jsonl`. Set `claude_bin` or `codex_bin` there if a service cannot locate a CLI. Adapt exclusions, project rules, and context to the new user. Preserve existing curated projects and manual assignments.

The Files sidebar uses registered file roots. Add only folders the user wants this local client to access. For a new chat, select that user's actual working directory in the conversation settings or use the native folder picker. Remote hosts require separate SSH and connector configuration; leave them unconfigured during the first local launch.

## Connect accounts

Open Workspace settings, then **Accounts**. Existing signed-in native profiles are discovered locally. Connected accounts show verified provider identity; incomplete sign-ins remain separate.

To add an account, choose **Connect account**, select Claude or Codex, and enter the account email. Finish the provider's login in the opened Terminal window, then choose **Check sign-in**. On a system without native Terminal integration, run the exact setup command shown by the interface as the intended user. Credentials stay in the provider's native credential stores and profile directories.

Each extra account uses its own Claude or Codex profile directory. Keep these directories private. Use the profile ID returned by the account APIs when configuring routing. A provider's internal user UUID is a different identity and cannot substitute for a configured profile ID.

Existing sessions resume with their native provider, host, and owner profile. Changing provider or account uses an explicit linked handoff. Unknown quota remains unknown. Confirm a model, account, folder, and location in the composer before any real send. Login and account verification do not require sending a paid chat message.

## Optional setup and development

Read `./install.sh --help` before installing session hooks, an agent skill, or a login service. These actions change the user's native client configuration. Verify the installed refresh path stays parse-only unless the user opts into model labeling. Service installation is optional; `./run.sh` works directly from a terminal.

Rebuild the frontend only when source changes require it:

```sh
cd workbench
npm ci
# Development tests are excluded from this release archive.
npm run build
```

Return to `app/` before running server commands. `python3 doctor.py` reports configuration and indexing problems. Read its proposed fixes before using `--fix`, which can install hooks or rebuild an index.

## Verify and hand over

Verify the local page loads, the intended transcript roots are indexed, a conversation opens, account identity is correct, and a user-selected file opens. Test composer behavior with mocks or inspect an unsent draft. Keep real sends, approvals, session stops, transcript deletion, and paid labeling outside installation QA.

Before restarting an existing service, inspect `/api/runs` and the persisted broker journals under `data/execution/`. Active runs must finish or be handled with the user's authorization. Closing a pane does not stop an agent.

Keep `data/`, account backups, native credential files, browser automation captures, output folders, and personal configuration out of shared packages. Browser drafts and layouts live in that browser's storage; copying source files does not migrate them. A successful handover gives the user the local URL, launch method, indexed providers, connected accounts, and any remaining configuration steps.

## Check automatic grouping and project maps

The first import groups unassigned human conversations by working directory without model calls. Review representative conversations from different folders and different purposes. A shared repository can contain several projects, so directory grouping is a starting point. Renaming a project or moving a chat is a manual decision that later refreshes must preserve.

For optional semantic grouping, explain that labels and short chat excerpts are sent through the user's configured provider CLI. Ask whether they want to spend that provider allowance. If they opt in, configure the available bulk/reasoning models, set `enable_model_labeling: true`, run `python3 pipeline.py`, then use `python3 classify.py --refine-workspaces` to refine untouched workspace seeds. Keep the user's manual placements intact. Do not use `--all` to reset their taxonomy.

`./run.sh` also starts the bundled read-only project-map companion. A direct backend service launch may require `python3 start_project_map.py`. Open a project map and check it reflects local conversations and recorded relationships. Do not describe inferred workspace membership as a proven dependency. General editable task dependency graphs remain future work.

## Supported integrations and completion report

Connect Claude Code and Codex accounts through Profile > Accounts. Other accounts, such as email or calendar providers, are outside this release. Never create placeholder connected accounts to imply a service works. A completed account check reports a verified provider identity; skipped or incomplete accounts remain visibly separate.

Offer the VS Code extension only after browser setup works. Build it with `python3 package.py` inside `vscode-library/`, install the printed VSIX, and open Library Chat. Both clients should observe the same existing conversation and broker state without starting a second provider session. Keep unsent drafts client-local.

End with a concise handover: local URL, launch method, discovered history roots, imported counts, grouping mode, verified accounts, selected file roots, map availability, and any skipped setup. Leave real paid messages to the user.

### Keep the project taxonomy current

Every refresh writes a local `data/classification-review.json` without model calls. It reviews already assigned chats as well as unassigned ones: recent or changed conversations, conflicts with private purpose rules, workspace-only seeds and duplicate project names. Structural suggestions flag empty project shells, single-child parents, and projects whose direct conversations are all archived. A zero unassigned count does not establish correct classification.

Keep the project list flat unless independent workstreams justify nesting. Archive completed conversations individually when the larger project remains active. This review runs after refreshes, without a wall-clock schedule.

Have your agent read this report periodically and after starting a new workstream. Inspect each conversation's actual purpose before moving it. Propose dedicated projects, parent groups, merges and archives as the work evolves. Preserve manual placements, including explicit Ungrouped choices. Applications discussed from a research folder can belong to an application project. Folder paths are evidence, not the final taxonomy.

Private `classification_rules` entries in `data/config.json` accept `project_id`, regex lists `any`, `all`, and `exclude`, and a plain-language `reason`. Rule order expresses priority; overlapping destinations are flagged for review. The report never changes assignments. Keep personal rules out of the shared repository. To regenerate the report alone, run `python3 project_review.py`.

If semantic review is useful, ask the user's agent to review a bounded batch of report items, using titles/descriptions first and selected user prompts only when needed. Agree on the provider/model and maximum batch before model calls; no background paid reclassification is scheduled. Apply accepted changes through the project/assignment APIs, then run the report again. Project records may use `parent_id`; parent views include descendant chats without duplicating the underlying assignment. Archiving a completed project remains an explicit decision, never an age-based inference.

## Upgrading from the flat layout

Older releases kept Python modules and private `data/` at the repository root. Current releases keep runtime files and private state in `app/`. Root launchers refuse to proceed while legacy private data remains, so an upgrade cannot silently start an empty library.

Have your agent perform this migration during an idle maintenance window:

1. Before updating, save the previous commit and back up private `data/` and installed hook/service configuration outside the checkout. Inspect the old service's `/api/runs` and execution journals. Wait for active provider runs to finish. Preserve unsent drafts and unsaved documents.
2. Stop the old Library service and graph companion only after confirming no active work. Update the checkout. Do not run the old and new services simultaneously against shared data.
3. Move legacy root `data/` into `app/data/`, preserving every private file. The packaged `app/data/config.example.json` is only a template. If `app/data/` already contains user state, stop and reconcile backups rather than replacing either tree. Keep the old backup until verification passes.
4. Inspect Claude/Codex hooks, launchd/systemd units, agent skills, and VS Code extension settings for absolute paths to the old root Python entry points. Remove only this installation's obsolete hook entries and update them to `app/session_start_refresh.py`. Re-run root `./install.sh --no-refresh` (add `--service` if previously enabled) to register the current paths. Remove the old service registration before installing its replacement; preserve unrelated hooks and services.
5. Start the new service with `./run.sh`. Confirm curated projects, accounts, history, drafts, graph access, and the extension endpoint before re-enabling automatic refresh. Retain the backup for rollback. Do not overwrite current state with an older snapshot if new work has begun.

A fresh installation needs no migration. Direct commands in this guide run from `app/`; root launchers are the supported entry points.
