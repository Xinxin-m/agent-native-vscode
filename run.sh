#!/usr/bin/env bash
set -euo pipefail
LIBRARY_REPO="$(cd "$(dirname "$0")" && pwd)"
if [[ -d "$LIBRARY_REPO/data" ]] && [[ -n "$(find "$LIBRARY_REPO/data" -mindepth 1 ! -name config.example.json -print -quit)" ]]; then
  echo "Legacy root/data detected. Stop here to preserve your library. Follow docs/AGENT-ONBOARDING.md: Upgrading from the flat layout." >&2
  exit 2
fi
exec bash "$LIBRARY_REPO/app/run.sh" "$@"
