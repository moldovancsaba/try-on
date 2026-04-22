# 👕 Local Virtual Try-On - Live Studio v2.0

**High-Fidelity | Identity Anchoring | Live Studio Previews**

This is the restored and refined "Golden Standard" of the Virtual Try-On platform. It represents a surgically cleaned foundation, optimized for Apple Silicon and professional terminal aesthetics.

## 🚀 Quick Start
1. **Initialize Engine**: Run the installer to prepare your environment and verify neural weights.
   ```bash
   chmod +x install.sh run.sh
   ./install.sh
   ```
2. **Launch Studio**:
   ```bash
   ./run.sh
   ```

## 💎 Key Features
- **Neural Streamer**: Real-time buildup previews. Every 4 steps are decoded and streamed to the UI with zero latency.
- **Identity Anchor (FaceID)**: Neural anchoring that locks the person's identity during the try-on to ensure 100% likeness.
- **Seed Suite (Mining & Refining)**: Integrated autonomous mining with "snap-to-lucky" (42, 1337) and precision lock logic.
- **Neural Inpainting**: High-fidelity garment integration using `CatVTON` + `ft-mse` VAE.
- **Studio Options**: Toggable FaceID, high-fidelity VAE switches, and GFPGAN restoration.
- **MPS Optimization**: Modern NumPy 2.x + ONNX 1.24 engine optimized for Apple Silicon (M1/M2/M3).

## 📁 System Map
- `app.py`: The single-file Live Studio engine.
- `/Users/Shared/Models/`: Central neural vault for all AI weights and identity models.
- `vendor/`: Core localized dependencies for CatVTON and Detectron2.

## 🛠️ Troubleshooting
- **Port Conflict**: If port 7860 is busy, run `lsof -i :7860` and `kill -9 <PID>`.
- **Slow Generation**: Ensure no other GPU-heavy apps (Video editors, 3D tools) are active. The engine uses `torch.mps.empty_cache()` to keep speed consistent.
- **Terminal Noise**: If warnings appear, check `DEVELOPMENT_MANTRA.md` for our silence protocol.

---
*Developed with 🛡️ by Antigravity*
