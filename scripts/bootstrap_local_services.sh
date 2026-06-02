#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
UID="$(id -u)"

PLISTS=(
  "com.tryon.camera-worker"
  "com.tryon.app-server"
)

if [ ! -d "$REPO_ROOT/.venv311" ]; then
  echo "Missing .venv311. Run install.sh first."
  exit 1
fi

if [ ! -x "$REPO_ROOT/.venv311/bin/python" ]; then
  echo "Python virtual env binary missing at $REPO_ROOT/.venv311/bin/python"
  exit 1
fi

"$REPO_ROOT/scripts/ensure_service_launchers.sh"

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$REPO_ROOT/queue/logs"

echo "Installing launchd plists from $(realpath "$REPO_ROOT/launchd")"
for plist in "${PLISTS[@]}"; do
  source_plist="$REPO_ROOT/launchd/${plist}.plist"
  target_plist="$LAUNCH_AGENTS_DIR/${plist}.plist"
  if [ ! -f "$source_plist" ]; then
    echo "Missing launchd profile: $source_plist"
    exit 1
  fi
  cp "$source_plist" "$target_plist"
  launchctl unload "$target_plist" 2>/dev/null || true
  launchctl load "$target_plist"
  launchctl kickstart -k "gui/$UID/$plist"
  echo "Launched service: $plist"

done

echo "Waiting for service readiness..."
MAX_ATTEMPTS=30
ATTEMPT=0
until "$REPO_ROOT/.venv311/bin/python" "$REPO_ROOT/scripts/service_healthcheck.py"; do
  ATTEMPT=$((ATTEMPT + 1))
  if [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
    echo "Service healthcheck did not pass after ${MAX_ATTEMPTS} attempts."
    exit 1
  fi
  sleep 2
done

echo "Local services installed and healthy."
echo "launchctl print gui/$UID/com.tryon.camera-worker"
launchctl print "gui/$UID/com.tryon.camera-worker"
echo "launchctl print gui/$UID/com.tryon.app-server"
launchctl print "gui/$UID/com.tryon.app-server"
