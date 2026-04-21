#!/bin/bash

# Navigate to project directory
cd "$(dirname "$0")"

# Find local venv
if [ -d ".venv311" ]; then
    VENV_DIR=".venv311"
elif [ -d ".venv" ]; then
    VENV_DIR=".venv"
else
    echo "No virtual environment found. Please run installation first."
    # Try to create one if python3.11 is available
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

# Run the app with precision log filtering
echo "Launching Try-On App using $VENV_DIR..."
export PYTORCH_ENABLE_MPS_FALLBACK=1
export SMF_CATVTON_USE_MPS=1

# Silence library noise for a clean professional terminal
python -u app.py 2>&1 | \
    grep --line-buffered -v "NOTE: Redirects" | \
    grep --line-buffered -v "Class AVF" | \
    grep --line-buffered -v "Checking local models" | \
    grep --line-buffered -v "visible to everyone"

