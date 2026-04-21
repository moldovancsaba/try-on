#!/bin/bash
set -e

echo "🚀 Starting Universal Virtual Try-On Installer for macOS..."

# 1. Environment Check
if ! command -v brew &> /dev/null; then
    echo "📦 Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

if ! brew list python@3.11 &> /dev/null; then
    echo "🐍 Python 3.11 not found. Installing..."
    brew install python@3.11
fi

# 2. Setup Virtual Environment
if [ ! -d ".venv311" ]; then
    echo "🛠️ Creating Virtual Environment..."
    /opt/homebrew/bin/python3.11 -m venv .venv311
fi

source .venv311/bin/activate
echo "📦 Installing primary dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
pip install huggingface_hub[cli] peft realesrgan basicsr gfpgan

# 3. Model Downloads
mkdir -p models/catvton
mkdir -p models/sd-inpainting
mkdir -p models/lcm_lora
mkdir -p models/upscalers

echo "📥 Checking/Downloading Model Weights (~8.5GB)..."

# LCM LoRA
if [ ! -f "models/lcm_lora/pytorch_lora_weights.safetensors" ]; then
    echo "Downloading LCM LoRA Booster..."
    huggingface-cli download latent-consistency/lcm-lora-sdv1-5 pytorch_lora_weights.safetensors --local-dir models/lcm_lora
fi

# CatVTON Weights
if [ ! -d "models/catvton/zhengchong_CatVTON" ]; then
    echo "Downloading CatVTON Main Weights..."
    huggingface-cli download Zheng-Chong/CatVTON --local-dir models/catvton/zhengchong_CatVTON
fi

# SD Inpainting (V1.5)
if [ ! -d "models/sd-inpainting/unet" ]; then
    echo "Downloading Stable Diffusion Inpainting Weights..."
    huggingface-cli download runwayml/stable-diffusion-inpainting --local-dir models/sd-inpainting
fi

# VAE
if [ ! -d "models/catvton/sd_vae_ft_mse" ]; then
    echo "Downloading SD VAE..."
    huggingface-cli download stabilityai/sd-vae-ft-mse --local-dir models/catvton/sd_vae_ft_mse
fi

# Upscalers
if [ ! -f "models/upscalers/RealESRGAN_x4plus.pth" ]; then
    echo "Downloading Real-ESRGAN..."
    curl -L https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth -o models/upscalers/RealESRGAN_x4plus.pth
fi
if [ ! -f "models/upscalers/GFPGANv1.3.pth" ]; then
    echo "Downloading GFPGAN..."
    curl -L https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.3.pth -o models/upscalers/GFPGANv1.3.pth
fi

echo "✅ Setup Complete!"
echo "🚀 To start the app, run: ./run.sh"

confirm_launch() {
    read -p "Do you want to launch the app now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        ./run.sh
    fi
}

confirm_launch
