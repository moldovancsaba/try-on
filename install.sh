#!/bin/bash

set -euo pipefail

# Installs the local Python environment and seeds the core shared-model dependencies.

cd "$(dirname "$0")"

echo "[try-on] Starting installation..."

MODELS_ROOT="${TRYON_MODELS_ROOT:-/Users/Shared/Models}"
VENV_DIR=".venv311"

if [ ! -d "$VENV_DIR" ]; then
    echo "[try-on] Creating fresh .venv311 with Python 3.11..."
    if command -v python3.11 >/dev/null 2>&1; then
        python3.11 -m venv "$VENV_DIR"
    elif [ -x /opt/homebrew/bin/python3.11 ]; then
        /opt/homebrew/bin/python3.11 -m venv "$VENV_DIR"
    else
        echo "[try-on] Python 3.11 is required but was not found."
        exit 1
    fi
fi

source "$VENV_DIR/bin/activate"

echo "[try-on] Syncing Python dependencies..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "[try-on] Synchronizing shared model vault at $MODELS_ROOT ..."
mkdir -p "$MODELS_ROOT/processors/upscalers"
python scripts/sync_models.py --profile core --write-manifest

echo "✅ Offline dependencies synchronized to $MODELS_ROOT"
echo "🚀 Installation complete. You are ready to run ./run.sh"

chmod +x run.sh
