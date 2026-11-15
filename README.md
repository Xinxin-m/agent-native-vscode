# Library Workspace

Your Claude Code and Codex conversations, projects, files, and running agents in one local workspace. Search old conversations, continue them in your browser or VS Code, and keep your history on your computer.

Library uses your installed provider CLIs and their accounts. A single local backend coordinates its clients, so accepted messages and run state are shared between the browser and the Library Chat extension.

## Start with your agent

Clone or download this repository, then give Claude Code or Codex this prompt:

> Read README.md and docs/AGENT-ONBOARDING.md. Install Library Workspace for me. Discover my local Claude Code and Codex histories, explain which folders you found, and import them without model calls. Help me connect the accounts I choose; let me skip any provider. Open official sign-in links when needed and keep passwords and login codes in the provider's own flow. Verify each connected identity in the dashboard. Help me select working folders, check automatic project grouping, and optionally install Library Chat in VS Code. Preserve existing settings, projects, drafts, and running sessions. Report what works and what still needs my input.

The full [agent onboarding guide](docs/AGENT-ONBOARDING.md) covers discovery, account connection, verification, troubleshooting, and upgrades.

## What you can do

- Search and organize local Claude Code and Codex conversations by project.
- Open chat and file tabs beside each other; preview images, Markdown, HTML, PDF, and source files.
- Continue conversations through the native provider and follow broker-managed work from multiple clients.
- Keep drafts while switching tabs, inspect pending questions and permissions, and see run status.
- Connect multiple Claude and Codex profiles and inspect their verified identities in Accounts.
- Group conversations automatically, then rename projects or move chats yourself.

The browser workspace has the broadest feature set. The optional VS Code extension continues existing conversations using the same backend. It keeps the native editor, terminal, and other extensions.

## Requirements

| Component | Requirement |
| --- | --- |
| Runtime | Python 3.11+, macOS or Linux, and a browser |
| History | Local Claude Code or Codex transcript files; authentication is unnecessary just to read them |
| Execution | The corresponding provider CLI, signed in, with access to the conversation's working folder |
| Frontend development | Node.js compatible with `workbench/package.json` dependencies and npm |
| VS Code client | Desktop VS Code 1.85+ and the optional local extension |

Python serves the bundled browser client using its standard library. Node is only needed for frontend development. Windows is not currently supported by the POSIX execution and file services.

Install providers using their official instructions: [Claude Code setup](https://code.claude.com/docs/en/setup) and [Codex CLI](https://github.com/openai/codex). Provider authentication and subscription requirements are separate from Library.

## Quick start

Clone the shared repository, then install:

```sh
git clone https://github.com/Xinxin-m/library-workspace.git
cd library-workspace
./install.sh --no-refresh
python3 pipeline.py --parse-only
./run.sh
```

Visit **http://127.0.0.1:8744/workbench**. The installer creates private configuration, merges lifecycle hooks without replacing existing hooks, and installs a helper skill. Optional `./install.sh --service` adds a login service. Review `./install.sh --help` for selective installation and removal.

For a first launch without changing provider hooks or installing a skill:

```sh
mkdir -p data
cp -n data/config.example.json data/config.json
python3 pipeline.py --parse-only
./run.sh
```

`cp -n` preserves an existing configuration. Fresh configuration disables model labeling. Importing history does not send messages or invoke a model.

## Connect your accounts

Open the **Profile** control, then **Accounts**. Review discovered native accounts or choose **Connect account** for Claude or Codex. Follow the displayed provider login flow and choose **Check sign-in**. An account counts as connected only after its identity is verified.

Each additional account gets its own native profile directory. Passwords, login codes, and provider tokens belong in the provider's own flow. The dashboard does not need them pasted into a conversation. On systems without automatic terminal opening, your agent can run the setup command shown by the interface.

Supported account connections are **Claude Code and Codex**. Gmail, calendars, Slack, and other service integrations are outside this release. A session owned by another native client may need an explicit handoff or branch before Library can continue it.

## How projects are grouped

A workspace directory is a useful first grouping signal. It is also common to discuss several projects in one directory or one project across several directories.

Fresh installs use deterministic workspace grouping without model calls. Optional AI labeling can improve titles, keywords, and topic grouping across folders. Set `enable_model_labeling` to `true` in your private configuration only after choosing the model and budget with your agent. The pipeline uses a lower-cost model for labels and bounded batches for topic classification. Calls go through the configured provider CLI and use that provider's allowance.

To refine untouched workspace groups after opting in, run `python3 classify.py --refine-workspaces`. Review the suggested grouping and model budget first.

Manual assignments take precedence. Keep your own project names and grouping rules in private configuration; they should never become defaults for another user. The [onboarding guide](docs/AGENT-ONBOARDING.md) explains the fields and migration checks.

## Workspaces and project graphs

The browser workspace supports mixed chat/file tabs and split panes. Project maps use the included read-only companion in `project-map/` on loopback port 53962. `./run.sh` starts or reuses it. For a service installation that starts the backend directly, run `python3 start_project_map.py` if the map is unavailable. Graphs show conversations, explicit agent relationships, and recorded inputs and outputs.

A general editable task dependency graph and arbitrary third-party account integrations remain future work. Conversation lineage is shown only when explicit evidence links a parent and child; a shared directory or similar title does not prove a dependency.

## VS Code

Keep the Library server running, then build the local extension:

```sh
cd vscode-library
python3 package.py
```

Install the printed VSIX through **Extensions: Install from VSIX**, then run **Library: Open Chat** from the Command Palette. Both clients connect to the same loopback service. Configure `library.endpoint` if you use another port. See the [extension guide](vscode-library/README.md) for client limits and shared-session behavior.

The extension is a local VSIX, not a Marketplace publication or a full Code-OSS fork.

## Configuration and private data

`data/config.example.json` is the portable template. Your `data/config.json` controls transcript roots, exclusions, port, model labeling, and project rules. Default histories are:

- Claude Code: `~/.claude/projects/`
- Codex: `~/.codex/sessions/`, with optional names from `~/.codex/session_index.jsonl`

Paths support `~` and environment variables. Viewing an imported conversation and resuming it have different requirements: resume also needs the original provider, account, host, and working directory.

Everything generated under `data/` is private and ignored by Git. This includes conversation text, titles, projects, account profiles, configuration, broker journals, logs, and file-access grants. Browser storage holds drafts and layouts; back it up separately when migrating machines.

## Maintain and troubleshoot

```sh
python3 doctor.py                 # diagnose configuration and indexing
python3 pipeline.py --parse-only  # import changed transcripts without models
python3 pipeline.py               # refresh under your configured labeling policy
```

If history is missing, check the transcript roots and exclusions before changing anything. A chat normally needs a user message and a real assistant answer; `min_answer_msgs: 0` includes unanswered chats. If a service cannot find a CLI, set `claude_bin` or `codex_bin` in private configuration. If sign-in remains incomplete, finish provider login and check again.

Before restarting a running backend, inspect `/api/runs` and the broker journals for active work. Closing a tab does not stop its agent. Keep existing configuration and private data during upgrades. `./install.sh --uninstall` removes installed integration and retains your library.

## Develop and share

```sh
cd workbench
npm ci
npm run build
```

The repository includes the browser build, so a fresh user can run Python directly. Rebuild after frontend edits. Development tests and release QA use fixtures rather than real paid messages.

`python3 package_workspace.py` creates an allowlisted source-and-assets archive with a file inventory, hashes, privacy scan, and dependency notices. It excludes private data, installed dependencies, old build hashes, browser captures, and local reports. Read the inventory before sharing. See [repository layout and release checks](docs/RELEASE.md).

Feel free to customize the interface, grouping rules, or provider workflows for your own use. Open issues with reproduction steps, runtime versions, and redacted logs. Never attach conversation exports, credentials, or private file paths to a public issue.

## Security and limitations

Library is a single-user local application. Its server binds to loopback and can read or edit authorized files, launch provider processes, and perform destructive transcript actions when requested. Keep it off public network interfaces. Separate people should run separate instances with separate operating-system accounts and credentials.

Provider transcript formats can change. Remote hosts require explicit SSH setup; remote attachments and some desktop operations have platform limits. Quota estimates and native session ownership can be incomplete. Unknown identity or quota must remain visible as unknown.

## License

MIT for this repository's application code. Bundled dependencies retain their own licenses; see `THIRD-PARTY-LICENSES.txt` in release packages and the notices alongside extension vendor assets.
