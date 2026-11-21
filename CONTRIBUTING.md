# Contributing to Library

We welcome changes in three areas: the UI layout, the visuals, and the prompts and agent roles that guide agents. Bug fixes anywhere are welcome too.

## Run from source

Follow the quick start in the [README](README.md), then work on the browser client:

```sh
cd app/workbench
npm ci            # install the locked dependencies
npm test          # unit tests (Vitest, the *.test.ts files in src/)
npm run dev       # dev server on 127.0.0.1, proxies /api to the server on port 8744
npm run build     # type check, then write the bundle to app/web/workbench/
```

Commit the rebuilt bundle with your source change, because users run the built client without Node. `python3 app/doctor.py` checks configuration and indexing. Test with synthetic transcripts in a temporary `HOME`, and never send paid messages to a provider as a test.

## Where to contribute

**UI layout.** Panes, tabs, sidebar and navigation live in `app/workbench/src/App.tsx`, `app/workbench/src/navigation/` and `app/workbench/src/docking.css`. A good change removes a step or a control: fewer clicks to reach a chat, a pane that opens where you expect, less on screen at once. Show it at a narrow and a wide window.

**Visuals.** Colors, type and spacing live in `app/workbench/src/theme.css` and the CSS file next to each component. Use the existing variables before adding new ones. A good change makes text easier to read or states clearer, and it works in both the light and the dark theme. Attach before and after screenshots taken with synthetic data.

**Prompts and agent roles.** `prompts/` holds general rules for agents in a long-lived workspace, `agents/` holds the roles an agent can take, and the root `AGENTS.md` holds the rules for this repository. A good change is a rule an agent actually needed: it names the situation, says what to do, and is short enough to be read on every run.

## Rules for every change

- **Parity.** One feature behaves the same in every client. Make UI changes in the browser workspace source (`app/workbench/src/`); `app/web/` holds the built bundle, a redirect page to it and the settings-insights script and style. A UI change also goes to the VS Code extension (`app/vscode-library/media/`) where that client has the same surface. If a client cannot have it, say so in the pull request.
- **Subtract.** Prefer removing to adding. No decoration, no new color without a meaning, no second way to do the same thing. One canonical copy of each file: edit in place and never add `v2` copies.
- **Generated menus.** Model lists come from the provider catalogs in code. Do not hard-code model names in the UI.
- **Keep user work safe.** Never discard drafts, stop running agents, or restart a server that has active runs as a side effect of your change.

## Propose a new agent role or prompt

Open an issue or a pull request that adds one Markdown file to `agents/` or `prompts/` and lists it in that folder's README. In the file, state what the agent does, when to use it, and what it must never do. In the pull request, describe a real task where the rule or role changed the result. Merge overlapping roles instead of adding a near duplicate.

## Privacy rules

- Never commit transcripts, `app/data/` (except `config.example.json`), account files, logs, browser captures or screenshots of real chats.
- Use invented names, paths and messages in tests, fixtures and screenshots. Use `example.com` for email addresses.
- Keep personal project names and grouping rules in your own `app/data/config.json`. They never become defaults.
- Before you push, run `python3 scripts/package_workspace.py --output /tmp/library-check` from the repository root (it needs `npm ci` in `app/workbench` first). It builds the release archive from an allowlist and fails, printing `file:line`, on home-directory paths, email addresses, phone numbers, `.edu` hostnames, credential-shaped strings and any term in your private `app/data/privacy-denylist.txt`. Read the `PRIVACY-SCAN.json` written beside the archive and review the file list yourself, because a scanner cannot catch every private sentence.
- Never paste conversation exports, tokens or private paths into an issue.

## Pull request checklist

- [ ] The change does one thing, and the description says what and why.
- [ ] `npm test` and `npm run build` pass, and the rebuilt bundle is included.
- [ ] The change is in every client that has the same surface, or the description says why not.
- [ ] Screenshots, if any, use synthetic data at a narrow and a wide window.
- [ ] The packager's privacy scan passes and no private file is in the diff.
- [ ] New files go in the folder the [release guide](docs/RELEASE.md) assigns them.
