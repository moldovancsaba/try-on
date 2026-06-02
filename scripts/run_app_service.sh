#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")/.."

export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
export PYTORCH_ENABLE_MPS_FALLBACK=1
export SMF_CATVTON_USE_MPS=1

mkdir -p queue/logs
"$PWD/scripts/ensure_service_launchers.sh"

exec /Users/Shared/Projects/try-on/.venv311/bin/tryon-app-server -u /Users/Shared/Projects/try-on/app.py
