#!/bin/bash

set -euo pipefail

# Navigate to project directory
cd "$(dirname "$0")"

# Find local venv
if [ -d ".venv311" ]; then
    VENV_DIR=".venv311"
elif [ -d ".venv" ]; then
    VENV_DIR=".venv"
else
    echo "No virtual environment found. Please run installation first."
    if [ -f "/opt/homebrew/bin/python3.11" ]; then
        echo "Creating venv with Python 3.11..."
        /opt/homebrew/bin/python3.11 -m venv .venv311
        VENV_DIR=".venv311"
    else
        echo "Creating venv with system python..."
        python3 -m venv .venv
        VENV_DIR=".venv"
    fi
fi

# Activate venv
source "$VENV_DIR/bin/activate"

# Force UTF-8 locale for Gradio/Orjson
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

echo "Launching Try-On App using $VENV_DIR..."
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    export PYTORCH_ENABLE_MPS_FALLBACK=1
    export SMF_CATVTON_USE_MPS=1
else
    unset PYTORCH_ENABLE_MPS_FALLBACK 2>/dev/null || true
    unset SMF_CATVTON_USE_MPS 2>/dev/null || true
fi

existing_pid="$(lsof -ti tcp:7860 -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
if [ -n "${existing_pid}" ]; then
    cmdline="$(ps -p "$existing_pid" -o command= 2>/dev/null || true)"
    case "$cmdline" in
        *"app.py"*|*"run.sh"*|*"gradio"*)
            echo "Stopping existing app process on port 7860 (PID $existing_pid)..."
            kill "$existing_pid" 2>/dev/null || true
            sleep 2
            if lsof -ti tcp:7860 -sTCP:LISTEN >/dev/null 2>&1; then
                echo "Port 7860 is still busy after graceful shutdown. Refusing to kill unrelated processes."
                exit 1
            fi
            ;;
        *)
            echo "Port 7860 is already in use by another process:"
            echo "  $cmdline"
            echo "Stop that process or change the app port before launching."
            exit 1
            ;;
    esac
fi

# Silence library noise for a clean terminal
python -u app.py 2>&1 | \
    grep --line-buffered -v "NOTE: Redirects" | \
    grep --line-buffered -v "Class AVF" | \
    grep --line-buffered -v "Checking local models" | \
    grep --line-buffered -v "visible to everyone"
