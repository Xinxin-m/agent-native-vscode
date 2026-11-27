#!/usr/bin/env bash
# Launch the local dashboard on localhost, or just open the one already running.
# Session hooks refresh the library when a chat starts or ends; the dashboard
# never polls transcript files. Set DASHBOARD_OPEN_BROWSER=1 to open a browser.
set -euo pipefail

cd "$(dirname "$0")"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else "Python 3.11+ is required")'
mkdir -p data

if [[ ! -f data/config.json ]]; then
  cp data/config.example.json data/config.json
  echo "Created data/config.json from the portable defaults."
  echo "Set user_context and project_rules in it for better titles and projects."
fi

PORT=$(python3 -c "import json; print(json.load(open('data/config.json')).get('port', 8744))")
ORIGIN="http://127.0.0.1:$PORT"
URL="$ORIGIN/workbench"

# Read-only project graph shares this installation's local index.
python3 start_project_map.py

open_browser() {
  if command -v open >/dev/null 2>&1; then ( sleep "$1"; open "$URL" ) &
  elif command -v xdg-open >/dev/null 2>&1; then ( sleep "$1"; xdg-open "$URL" ) &
  fi
}

# Already serving -- typically the login service installed by ./install.sh
# --service. Starting a second copy would only fail to bind the port.
if python3 - "$ORIGIN" <<'PY' 2>/dev/null
import sys, urllib.request
urllib.request.urlopen(sys.argv[1] + "/api/revision", timeout=3).read()
PY
then
  echo "Already serving at $URL"
  open_browser 0
  exit 0
fi

[[ "${DASHBOARD_OPEN_BROWSER:-0}" == "1" ]] && open_browser 1

echo "Serving at $URL  (ctrl-c to stop)"
exec python3 server.py
