#!/bin/bash

# 👕 Local Virtual Try-On - Standard Installer
# Ensures your environment and neural weights are perfectly aligned.

echo "🛡️ Starting Golden Standard Installation..."

# 1. Environment Check
VENV_DIR=".venv311"
if [ ! -d "$VENV_DIR" ]; then
    echo "[try-on] Creating fresh .venv311 with Python 3.11..."
    /opt/homebrew/bin/python3.11 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# 2. Dependency Audit
echo "[try-on] Syncing neural dependencies..."
pip install --upgrade pip
pip install -r requirements.txt --quiet

# 3. Neural Weight Audit
echo "[try-on] Verifying global neural weights..."
MISSING_WEIGHTS=0

check_weight() {
    if [ ! -f "$1" ] && [ ! -d "$1" ]; then
        echo "❌ MISSING: $1"
        MISSING_WEIGHTS=$((MISSING_WEIGHTS + 1))
    else
        echo "✅ FOUND: $1"
    fi
}

# Core Models
check_weight "/Users/Shared/Models/sd/v1-5-pruned-emaonly-inpainting"
check_weight "/Users/Shared/Models/loras/lcm/pytorch_lora_weights.safetensors"

# Enhancer Models
check_weight "/Users/Shared/Models/analysis/insightface/models/antelopev2"
check_weight "/Users/Shared/Models/analysis/gfpgan/GFPGANv1.4.pth"

if [ $MISSING_WEIGHTS -gt 0 ]; then
    echo "⚠️  Some weights are missing. Please ensure all required models are synced to /Users/Shared/Models/."
else
    echo "🚀 installation Complete. You are ready to run ./run.sh"
fi

chmod +x run.sh
