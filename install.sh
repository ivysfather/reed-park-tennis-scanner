#!/bin/bash
# Installs two launchd jobs:
#   local.reedpark.tennis.open   hourly at :14 (acts 7:20-8:30 PT) -> waits for the 8:15 release
#   local.reedpark.tennis.watch  every 15 min  -> cancellation alerts
# Re-run any time to reload. ./install.sh uninstall to remove.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$DIR/logs"

for job in open watch; do
  launchctl bootout "gui/$(id -u)/local.reedpark.tennis.$job" 2>/dev/null || true
  rm -f "$AGENTS/local.reedpark.tennis.$job.plist"
done

if [[ "${1:-}" == "uninstall" ]]; then
  echo "Removed both jobs."
  exit 0
fi

if [[ ! -f "$DIR/.env" ]]; then
  echo "Missing $DIR/.env — copy .env.example and fill in your settings first."
  exit 1
fi

write_plist() {
  local job="$1" schedule="$2"
  cat > "$AGENTS/local.reedpark.tennis.$job.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.reedpark.tennis.$job</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DIR/scanner.py</string>
    <string>$job</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  $schedule
  <key>StandardOutPath</key><string>$DIR/logs/$job.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/$job.log</string>
</dict>
</plist>
EOF
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/local.reedpark.tennis.$job.plist"
}

# launchd uses the Mac's local time zone, which may not be Pacific. Fire at :14
# every hour; scanner.py only acts when it's 8am PT.
write_plist open '<key>StartCalendarInterval</key><dict><key>Minute</key><integer>14</integer></dict>'
write_plist watch '<key>StartInterval</key><integer>900</integer>'

echo "Installed. Logs: $DIR/logs/"
launchctl list | grep local.reedpark.tennis || true
