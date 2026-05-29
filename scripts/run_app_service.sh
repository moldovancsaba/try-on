#!/bin/bash

set -euo pipefail

cd "$(dirname "$0")/.."

export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
export PYTORCH_ENABLE_MPS_FALLBACK=1
export SMF_CATVTON_USE_MPS=1

mkdir -p queue/logs

exec script -q queue/logs/app.pty.log /bin/bash -lc '
  cd /Users/Shared/Projects/try-on
  export LANG=en_US.UTF-8
  export LC_ALL=en_US.UTF-8
  export PYTORCH_ENABLE_MPS_FALLBACK=1
  export SMF_CATVTON_USE_MPS=1
  exec /Users/Shared/Projects/try-on/.venv311/bin/python -u /Users/Shared/Projects/try-on/app.py
'
