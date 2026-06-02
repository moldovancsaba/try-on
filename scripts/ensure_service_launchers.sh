#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$REPO_ROOT/.venv311/bin/python"
VENV_BIN="$REPO_ROOT/.venv311/bin"
LAUNCHER_DIR="$REPO_ROOT/.service-bin"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python virtual env binary missing at $PYTHON_BIN"
  exit 1
fi

mkdir -p "$LAUNCHER_DIR"
ln -sf "$PYTHON_BIN" "$VENV_BIN/tryon-app-server"
ln -sf "$PYTHON_BIN" "$VENV_BIN/tryon-queue-worker"
ln -sf "$VENV_BIN/tryon-app-server" "$LAUNCHER_DIR/tryon-app-server"
ln -sf "$VENV_BIN/tryon-queue-worker" "$LAUNCHER_DIR/tryon-queue-worker"
