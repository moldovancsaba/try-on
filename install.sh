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

# 3. Neural Weight Synchronization
echo "[try-on] Synchronizing Centralized Neural Hub (/Users/Shared/Models/)..."
MODELS_ROOT="/Users/Shared/Models"
mkdir -p "$MODELS_ROOT"

echo ">> Downloading CatVTON Backend & Segmentation Models..."
huggingface-cli download zhengchong/CatVTON --include "*DensePose*" "*SCHP*" --local-dir "$MODELS_ROOT/processors/catvton-segmentation"

echo ">> Downloading LCM LoRA (Fast Drafting)..."
huggingface-cli download latent-consistency/lcm-lora-sdv1-5 pytorch_lora_weights.safetensors --local-dir "$MODELS_ROOT/loras/sd15-lcm"

echo ">> Downloading Inpainting Base (Stable Diffusion v1.5)..."
huggingface-cli download runwayml/stable-diffusion-inpainting --local-dir "$MODELS_ROOT/sd/v1-5-pruned-emaonly-inpainting"

echo ">> Downloading InsightFace Anchors (antelopev2)..."
huggingface-cli download DIAMONIK7777/antelopev2 --local-dir "$MODELS_ROOT/analysis/insightface/models/antelopev2"

echo ">> Downloading GFPGAN & RealESRGAN (VFX Post-Processing)..."
mkdir -p "$MODELS_ROOT/processors/face-restoration"
mkdir -p "$MODELS_ROOT/processors/upscalers"
curl -L -s "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth" -o "$MODELS_ROOT/processors/face-restoration/GFPGANv1.4.pth"
curl -L -s "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth" -o "$MODELS_ROOT/processors/upscalers/RealESRGAN_x4plus.pth"

echo "✅ All neural weights successfully synchronized to $MODELS_ROOT"

echo "🚀 Installation Complete. You are ready to run ./run.sh"

chmod +x run.sh
