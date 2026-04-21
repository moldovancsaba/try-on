# 👕 Local Virtual Try-On - Standard Baseline (V1)

**Peak Stability | Total Silence | Studio Quality**

This is the restored and refined "Golden Standard" of the Virtual Try-On platform. It combines the high-fidelity neural logic of the early core with modern optimizations for Apple Silicon and a professional terminal interface.

## 💎 Features
- **Neural Inpainting**: Powered by `CatVTON` for seamless clothing integration.
- **Studio Enhancements**: Integrated `GFPGAN` for face restoration and `Real-ESRGAN` for 4x super-resolution.
- **Silent Launch**: Neural handshakes and libraries have been surgically tuned to achieve 100% terminal silence.
- **Offline Ready**: All required face-detection and parsing models (200MB+) are pre-loaded into `gfpgan/weights`.
- **MPS Optimized**: Native Apple Silicon support with automatic GPU cache management for consistent 10s-30s inference.

## 🚀 Getting Started
1. **Prepare Environment**: Use Python 3.11 with the provided `.venv311`.
2. **Launch App**: 
   ```bash
   ./run.sh
   ```
3. **Studio Experience**: The terminal will stay 100% silent during launch and will show a clean `tqdm` progress bar during generation.

## ⚖️ Neural Handshakes
- **LCM LoRA**: Synced to `mps` for optimized 4-8 step draft generation.
- **Scheduler Sync**: Automatically switches between `LCMScheduler` (Draft) and `EulerAncestral` (Production) based on steps.

---
*Maintained with 🛡️ by Antigravity*
