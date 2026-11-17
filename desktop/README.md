# Library Code

A full Code-OSS source fork with Library conversations, projects, accounts and rendered documents in the native workbench. One shared Library service owns provider execution. The native client persists its drafts separately from the shared conversation history.

## Build

The upstream checkout is `vscode/`; its pinned base is in `docs/upstream.json`. Install the Node version in `vscode/.nvmrc` and the exact package manager in `vscode/package.json`. Follow upstream's platform prerequisites, including Xcode command-line tools on macOS.

1. In `vscode`, run `pnpm install --frozen-lockfile`.
2. In `integration/ui`, run `npm ci` (this separate frontend uses its own npm lockfile).
3. From this directory, run `python3 integration/build.py` to build and stage the builtin client.
4. In `vscode`, run `pnpm run compile-client` for development or `pnpm run gulp vscode-darwin-arm64` for the macOS Apple Silicon package.
5. For development use `integration/launch.sh`, which chooses an isolated profile. The installed daily app uses its normal Library Code profile.
6. Sign and verify the complete candidate, record its hash, then use the guarded installer. Follow [Build and update](docs/build-and-update.md); `--restart` alone never installs a build.

Configure the local shared service through the `library.endpoint` setting. Only HTTP loopback origins are accepted. The app package does not include transcripts, provider credentials, user profiles or datasets. Provider metadata discovery initializes the CLI without an inference turn; conversation execution remains broker-owned.

## Verification

After `pnpm run compile-client` in `vscode/`, `node --test integration/extension/test/*.test.cjs` checks extension behavior. `npm test` in `integration/ui` checks client state. Native interaction checks require the real Electron workbench; mock browser tests alone do not establish native drag/drop or installed-app delivery.

Never restart a shared Library backend while provider runs are active. Before a client update, capture existing draft hashes and process identities, then verify them after reloading the same window. Preserve native unsaved document buffers.

## Source handoff

`python3 integration/export_source.py OUTPUT.tar.gz` creates an explicit source allowlist, upstream patch, new native source files and hash manifest. It excludes the private experiment docs, backend data, screenshots, logs and user profiles. Reconstruct the fork by checking out the recorded upstream base, applying `upstream.patch`, then copying `native-additions/` into the upstream checkout and running the build steps above. The current local checkout retains full upstream history.

This fork retains upstream licenses and notices. Proprietary Microsoft Marketplace, Settings Sync and remote extensions are not implied by a Code-OSS build. Library's built-in remote file connector currently supplies browsing and read access; remote writes remain unavailable. Native remote filename/content search requires the matching shared-service implementation.
