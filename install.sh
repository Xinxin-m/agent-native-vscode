#!/usr/bin/env bash
# install.sh — set up the chat library on this machine.
#
#   ./install.sh                 config + hooks + skill, then a first refresh
#   ./install.sh --hooks-only    only wire the session hooks
#   ./install.sh --skill-only    only install the agent skill
#   ./install.sh --service       also keep the server running at login
#   ./install.sh --no-refresh    skip the first (slow) refresh
#   ./install.sh --uninstall     remove hooks, skill, and service (keeps your data)
#
# Everything is additive and idempotent: hooks are MERGED into your existing
# settings files by key, never overwriting other hooks, and re-running changes
# nothing that is already correct. Your chats, labels, and projects live in
# data/ and are never touched here.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$HERE/session_start_refresh.py"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CODEX_HOOKS="$HOME/.codex/hooks.json"
SKILL_DIR="$HOME/.claude/skills/chat-dashboard"
PLIST="$HOME/Library/LaunchAgents/com.chatdashboard.server.plist"
SYSTEMD_UNIT="$HOME/.config/systemd/user/chat-dashboard.service"

DO_CONFIG=1 DO_HOOKS=1 DO_SKILL=1 DO_SERVICE=0 DO_REFRESH=1 DO_UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --hooks-only)  DO_CONFIG=0; DO_SKILL=0; DO_REFRESH=0 ;;
    --skill-only)  DO_CONFIG=0; DO_HOOKS=0; DO_REFRESH=0 ;;
    --service)     DO_SERVICE=1 ;;
    --no-refresh)  DO_REFRESH=0 ;;
    --uninstall)   DO_UNINSTALL=1 ;;
    -h|--help)     sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say() { printf '  %s\n' "$*"; }

# ---------------------------------------------------------------- python found
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || { echo "Python 3.11+ is required" >&2; exit 1; }

"$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else "Python 3.11+ is required")'

# ------------------------------------------------------- merge one hook by key
# usage: wire_hook <settings-file> <event> <matcher> <timeout>
# Adds our hook to that event only if no entry already points at our script.
wire_hook() {
  local file="$1" event="$2" matcher="$3" timeout="$4"
  mkdir -p "$(dirname "$file")"
  [ -f "$file" ] || echo '{}' > "$file"
  HOOK="$HOOK" FILE="$file" EVENT="$event" MATCHER="$matcher" TIMEOUT="$timeout" \
  "$PY" - <<'PY'
import json, os, sys
path, event = os.environ["FILE"], os.environ["EVENT"]
hook, matcher = os.environ["HOOK"], os.environ["MATCHER"]
timeout = int(os.environ["TIMEOUT"])
try:
    with open(path) as fh:
        blob = json.load(fh)
except Exception as error:
    if os.path.getsize(path) > 2:                       # do not clobber content
        sys.exit(f"    {path} is not valid JSON ({error}); fix it and rerun")
    blob = {}
groups = blob.setdefault("hooks", {}).setdefault(event, [])
for group in groups:
    for entry in group.get("hooks", []) or []:
        if hook in str(entry.get("command", "")):
            print(f"    {event}: already wired"); sys.exit(0)
groups.append({"matcher": matcher,
               "hooks": [{"type": "command", "command": f'python3 "{hook}"',
                          "async": True, "timeout": timeout}]})
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(blob, fh, indent=2)
os.replace(tmp, path)
print(f"    {event}: wired")
PY
}

unwire_hook() {
  local file="$1"
  [ -f "$file" ] || return 0
  HOOK="$HOOK" FILE="$file" "$PY" - <<'PY'
import json, os
path, hook = os.environ["FILE"], os.environ["HOOK"]
try:
    with open(path) as fh:
        blob = json.load(fh)
except Exception:
    raise SystemExit(0)
hooks = blob.get("hooks", {}) or {}
removed = 0
for event, groups in list(hooks.items()):
    kept = []
    for group in groups:
        entries = [e for e in (group.get("hooks") or [])
                   if hook not in str(e.get("command", ""))]
        removed += len(group.get("hooks") or []) - len(entries)
        if entries:
            group["hooks"] = entries
            kept.append(group)
    if kept:
        hooks[event] = kept
    else:
        hooks.pop(event)
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(blob, fh, indent=2)
os.replace(tmp, path)
print(f"    removed {removed} hook(s) from {os.path.basename(path)}")
PY
}

# ------------------------------------------------------------------- uninstall
if [ "$DO_UNINSTALL" = 1 ]; then
  echo "Removing hooks, skill, and service (your data/ stays):"
  unwire_hook "$CLAUDE_SETTINGS"
  unwire_hook "$CODEX_HOOKS"
  [ -d "$SKILL_DIR" ] && rm -rf "$SKILL_DIR" && say "removed $SKILL_DIR"
  if [ -f "$PLIST" ]; then launchctl unload "$PLIST" 2>/dev/null || true; rm -f "$PLIST"; say "removed launch agent"; fi
  if [ -f "$SYSTEMD_UNIT" ]; then systemctl --user disable --now chat-dashboard 2>/dev/null || true; rm -f "$SYSTEMD_UNIT"; say "removed systemd unit"; fi
  echo "Done. Delete $HERE to remove the tool itself."
  exit 0
fi

echo "Installing the chat library from $HERE"

# ----------------------------------------------------------------- 1. config
if [ "$DO_CONFIG" = 1 ]; then
  echo "config:"
  mkdir -p "$HERE/data"
  if [ -f "$HERE/data/config.json" ]; then
    say "data/config.json exists, left alone"
  else
    cp "$HERE/data/config.example.json" "$HERE/data/config.json"
    say "created data/config.json -- edit user_context and project_rules to fit you"
  fi
fi

# ------------------------------------------------------------------ 2. hooks
if [ "$DO_HOOKS" = 1 ]; then
  echo "hooks (Claude Code):"
  # startup|resume|clear: every way a chat window begins.
  wire_hook "$CLAUDE_SETTINGS" SessionStart 'startup|resume|clear' 2400
  # SessionEnd: capture the chat that just finished without waiting for the
  # next launch. The refresh detaches, so the short budget is not a problem.
  wire_hook "$CLAUDE_SETTINGS" SessionEnd '*' 60
  if command -v codex >/dev/null 2>&1; then
    echo "hooks (Codex):"
    wire_hook "$CODEX_HOOKS" SessionStart '^startup$' 2400
    # Stop fires after every reply, in new and resumed chats alike; SessionEnd
    # catches the last one. Both refreshes detach, so Codex never waits.
    wire_hook "$CODEX_HOOKS" Stop '*' 60
    wire_hook "$CODEX_HOOKS" SessionEnd '*' 3
  fi
fi

# ------------------------------------------------------------------ 3. skill
if [ "$DO_SKILL" = 1 ] && [ -d "$HERE/skill" ]; then
  echo "agent skill:"
  mkdir -p "$SKILL_DIR"
  cp -R "$HERE/skill/." "$SKILL_DIR/"
  # the skill needs to know where this checkout lives
  "$PY" - "$SKILL_DIR/SKILL.md" "$HERE" <<'PY'
import sys
path, root = sys.argv[1], sys.argv[2]
text = open(path).read().replace("{{TOOL_ROOT}}", root)
open(path, "w").write(text)
PY
  say "installed to $SKILL_DIR"
fi

# ---------------------------------------------------------------- 4. service
if [ "$DO_SERVICE" = 1 ]; then
  echo "auto-start service:"
  PORT="$("$PY" -c "import json;print(json.load(open('$HERE/data/config.json')).get('port',8744))" 2>/dev/null || echo 8744)"
  # Pin an absolute, durable interpreter. A service PATH is minimal, so
  # `env python3` can resolve to a different (older) python than your shell
  # uses -- and the server launches the pipeline stages with its own
  # interpreter. Ephemeral environments (uv caches, .venv, conda envs) are
  # skipped: they get garbage-collected and the service would stop starting.
  PY_ABS=""
  for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v "$PY")" /usr/bin/python3; do
    case "$candidate" in
      ""|*/.cache/*|*/uv/*|*/.venv/*|*/envs/*|*/conda*) continue ;;
    esac
    [ -x "$candidate" ] || continue
    "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null \
      && { PY_ABS="$candidate"; break; }
  done
  [ -n "$PY_ABS" ] || { echo "    no durable python3 found; skipping service" >&2; DO_SERVICE=0; }
fi
if [ "$DO_SERVICE" = 1 ]; then
  say "service interpreter: $PY_ABS ($("$PY_ABS" -c 'import sys;print(sys.version.split()[0])'))"
  if [ "$(uname)" = "Darwin" ]; then
    mkdir -p "$(dirname "$PLIST")"
    cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.chatdashboard.server</string>
  <key>ProgramArguments</key>
  <array><string>$PY_ABS</string><string>$HERE/server.py</string></array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>EnvironmentVariables</key>
  <dict>
    <!-- launchd's default PATH cannot see the agent CLIs; clis.py also widens
         the search, this makes anything they spawn work too -->
    <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HERE/data/server.log</string>
  <key>StandardErrorPath</key><string>$HERE/data/server.log</string>
</dict>
</plist>
PLISTEOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    say "launch agent loaded -- http://127.0.0.1:$PORT starts at login"
  else
    mkdir -p "$(dirname "$SYSTEMD_UNIT")"
    cat > "$SYSTEMD_UNIT" <<UNITEOF
[Unit]
Description=Local agent chat library
[Service]
ExecStart=$PY_ABS $HERE/server.py
WorkingDirectory=$HERE
Environment=PATH=$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
Restart=always
[Install]
WantedBy=default.target
UNITEOF
    systemctl --user daemon-reload
    systemctl --user enable --now chat-dashboard
    say "systemd user unit enabled -- http://127.0.0.1:$PORT"
  fi
fi

# --------------------------------------------------------- 5. first refresh
if [ "$DO_REFRESH" = 1 ]; then
  echo "first refresh (deterministic import; no model calls):"
  "$PY" "$HERE/pipeline.py" --reason install --parse-only || true
fi

echo
"$PY" "$HERE/doctor.py" || true
echo
echo "Start it with:  cd $HERE && ./run.sh"
