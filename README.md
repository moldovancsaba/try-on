# 👕 Local Virtual Try-On - Live Studio v3.0 (Pro-Control Edition)

**High-Fidelity | Safety Matrix | Deep Originality Preservation**

This is the fully matured, "Golden Standard" Virtual Try-On Studio. Engineered specifically for Apple Silicon (MPS), the engine is mathematically stabilized with dynamic safety clamps to prevent crashes, alongside an entirely new suite of post-processing VFX tools for exact visual preservation.

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

## 💎 V3.0 Pro Features
- **Surgical Head Paste**: Extracts your exact face, hair, and sunglasses from the original photo and alpha-blends it pixel-perfectly onto the final AI generation. 100% molecular originality guaranteed.
- **LCM Safety Circuit Break**: Mathematical engine interlock that perfectly bounds Guidance Scale under `2.0` when running Fast Drafts (LCM), making "deep-fried" crashes impossible.
- **Auto-Preset Snap System**: Toggling between Fast Draft and High Quality instantly configures all 7 hidden sliders (Steps, Guidance, Padding, Blend, Sampler) to their mathematically optimal safe zones.
- **Deep Clean Plate**: A true green-screen mode. Automatically calculates the silhouette of your entire body, and securely isolates the Generated Try-On from your provided original untouched background.
- **Mask Dilation Engine**: Custom padding parameters (-10 to +30) to surgically expand AI rendering zones and completely eradicate original clothing "ghosting."
- **Fractional Face Restoration**: Variable GFPGAN slider allowing you to seamlessly mix your original skin pores and freckles alongside AI face-symmetry enhancements.
- **Karras Optimization**: The backend now natively overrides standard rendering by substituting `DPM++ 2M Karras` to maximize micro-texture clarity at 30+ steps.

## 📁 System Map
- `app.py`: The single-file Live Studio engine and UI architecture.
- `/Users/Shared/Models/`: Central neural vault for all isolated AI weights globally.
- `vendor/`: Core localized dependencies for CatVTON and Detectron2 isolated from upstream pipeline regressions.

## 🛠️ Advanced Operations
- **Fast Draft (8 Steps)**: Will execute instantly, but is restricted to testing physical clothing fit and proportion. (Ignore the harmless Diffusers console warnings).
- **High Quality (30+ Steps)**: Triggers the `DPM++ 2M Karras` scheduler and unlocks deep texture unsharp masking and Surgical Head features.

---
*Developed with 🛡️ by Antigravity*
