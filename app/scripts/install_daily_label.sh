#!/usr/bin/env bash
# Install (or refresh) the once-a-day relabel job: com.chatdashboard.daily-label.
#   scripts/install_daily_label.sh [python] [--remove]
# Runs `pipeline.py --daily` at config `daily_label_time` (default 04:45 local).
# launchd runs a missed job when the Mac wakes. macOS: launch agent; Linux: user timer.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${1:-$(command -v python3)}"
[ "$PY" = "--remove" ] && PY="$(command -v python3)"
LABEL=com.chatdashboard.daily-label
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UNIT_DIR="$HOME/.config/systemd/user"

if [ "${*: -1}" = "--remove" ]; then
  if [ -f "$PLIST" ]; then launchctl unload "$PLIST" 2>/dev/null || true; rm -f "$PLIST"; echo "removed $LABEL"; fi
  rm -f "$UNIT_DIR/$LABEL.timer" "$UNIT_DIR/$LABEL.service" 2>/dev/null || true
  exit 0
fi

TIME="$("$PY" -c "import json;print(json.load(open('$HERE/data/config.json')).get('daily_label_time') or '04:45')" 2>/dev/null || echo 04:45)"
HOUR="$((10#${TIME%%:*}))"; MINUTE="$((10#${TIME##*:}))"

if [ "$(uname)" = "Darwin" ]; then
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$HERE/pipeline.py</string><string>--daily</string><string>--wait</string><string>--reason</string><string>daily-label</string><string>--quiet</string></array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MINUTE</integer></dict>
  <key>Nice</key><integer>10</integer>
  <key>LowPriorityIO</key><true/>
  <key>StandardOutPath</key><string>$HERE/data/daily-label.log</string>
  <key>StandardErrorPath</key><string>$HERE/data/daily-label.log</string>
</dict>
</plist>
PLISTEOF
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "daily label job loaded: $TIME local ($LABEL)"
else
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/$LABEL.service" <<UNITEOF
[Unit]
Description=Chat dashboard daily relabel
[Service]
Type=oneshot
ExecStart=$PY $HERE/pipeline.py --daily --wait --reason daily-label --quiet
WorkingDirectory=$HERE
UNITEOF
  cat > "$UNIT_DIR/$LABEL.timer" <<UNITEOF
[Unit]
Description=Chat dashboard daily relabel
[Timer]
OnCalendar=*-*-* $(printf '%02d:%02d' "$HOUR" "$MINUTE"):00
Persistent=true
[Install]
WantedBy=timers.target
UNITEOF
  systemctl --user daemon-reload
  systemctl --user enable --now "$LABEL.timer"
  echo "daily label timer enabled: $TIME ($LABEL)"
fi
