#!/bin/bash

set -euo pipefail

# 👕 Local Virtual Try-On - Standard Installer
# Ensures the local environment and offline model cache match the runtime contract.

cd "$(dirname "$0")"

echo "🛡️ Starting Golden Standard Installation..."

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

echo "[try-on] Synchronizing offline model hub at $MODELS_ROOT ..."
mkdir -p \
    "$MODELS_ROOT/processors/catvton-segmentation" \
    "$MODELS_ROOT/checkpoints/sd15-inpainting" \
    "$MODELS_ROOT/vae/sd15-vae-ft-mse" \
    "$MODELS_ROOT/processors/face-restoration" \
    "$MODELS_ROOT/processors/upscalers"

echo ">> Downloading CatVTON segmentation dependencies..."
huggingface-cli download zhengchong/CatVTON \
    --include "*DensePose*" "*SCHP*" \
    --local-dir "$MODELS_ROOT/processors/catvton-segmentation"

echo ">> Downloading Stable Diffusion v1.5 inpainting base..."
huggingface-cli download runwayml/stable-diffusion-inpainting \
    --local-dir "$MODELS_ROOT/checkpoints/sd15-inpainting"

echo ">> Downloading VAE checkpoint..."
huggingface-cli download stabilityai/sd-vae-ft-mse \
    --local-dir "$MODELS_ROOT/vae/sd15-vae-ft-mse"

echo ">> Downloading GFPGAN checkpoint..."
curl -L -s \
    "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth" \
    -o "$MODELS_ROOT/processors/face-restoration/GFPGANv1.4.pth"

echo ">> Downloading GFPGAN auxiliary weights for offline runtime..."
curl -L -s \
    "https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth" \
    -o "$MODELS_ROOT/processors/face-restoration/detection_Resnet50_Final.pth"
curl -L -s \
    "https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth" \
    -o "$MODELS_ROOT/processors/face-restoration/parsing_parsenet.pth"

if [ -f "$MODELS_ROOT/processors/upscalers/GFPGANv1.3.pth" ] && [ ! -f "$MODELS_ROOT/processors/face-restoration/GFPGANv1.3.pth" ]; then
    cp "$MODELS_ROOT/processors/upscalers/GFPGANv1.3.pth" "$MODELS_ROOT/processors/face-restoration/GFPGANv1.3.pth"
fi

echo "✅ Offline dependencies synchronized to $MODELS_ROOT"
echo "🚀 Installation complete. You are ready to run ./run.sh"

chmod +x run.sh
