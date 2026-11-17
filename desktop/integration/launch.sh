#!/usr/bin/env bash
set -euo pipefail
EXPERIMENT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NODE_VERSION="$(cat "$EXPERIMENT_ROOT/vscode/.nvmrc")"
export PATH="$EXPERIMENT_ROOT/output/toolchain/node-v$NODE_VERSION-darwin-arm64/bin:$EXPERIMENT_ROOT/output/toolchain/pnpm/node_modules/.bin:$PATH"
cd "$EXPERIMENT_ROOT/vscode"
export VSCODE_SKIP_PRELAUNCH=1
exec ./scripts/code.sh --user-data-dir "$EXPERIMENT_ROOT/output/library-profile" --extensions-dir "$EXPERIMENT_ROOT/output/library-extensions" --disable-extension GitHub.copilot-chat --skip-welcome --skip-release-notes "$@"
