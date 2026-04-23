# 👕 Local Virtual Try-On - Live Studio v3.0 (Pro-Control Edition)

**High-Fidelity | Identity Anchoring | Live Studio Previews | Scientific Spatial Constraints**

This repository is the fully matured, "Golden Standard" Virtual Try-On Studio. It represents a significantly modified, production-grade evolution of the open-source Virtual Try-On paradigm. It solves the fundamental limitations of latent diffusion (hallucinations, cape artifacts, text destruction) by wrapping the core neural engine in a **Custom Mathematical Architecture**. 

Engineered specifically for Apple Silicon (MPS), the engine is mathematically stabilized with dynamic safety clamps to prevent crashes, alongside an entirely new suite of post-processing VFX tools for exact visual preservation.

---

## 🏛️ The Centralized Neural Hub Architecture

To prevent duplicate 10GB+ neural network downloads across different AI projects, **this repository enforces a Centralized Neural Hub**.

By default, the engine requires all AI models to live at a specific absolute path on your machine:
📁 **`/Users/Shared/Models/`**

### ⚠️ If you are on Windows or Linux:
You **must** modify the `_MODELS_ROOT` path in the source code to match your operating system, or use a UNIX-like path mapping.
1. Open `app.py`.
2. Locate Line 51: `_MODELS_ROOT = Path("/Users/Shared/Models")`
3. Change this to your preferred universal model directory (e.g., `C:/Shared/Models` or `/home/user/Models`).

---

## 🚀 Quick Start
1. **Initialize Engine**: Run the installer to prepare your environment and verify neural weights. The script will automatically construct the Centralized Hub and use `huggingface-cli` to securely download the hundreds of megabytes of required weights directly into it.
   ```bash
   chmod +x install.sh run.sh
   ./install.sh
   ```
2. **Launch Studio**:
   ```bash
   ./run.sh
   ```

---

## 💎 V3.0 Pro Features
- **Surgical Head Paste**: Extracts your exact face, hair, and sunglasses from the original photo and alpha-blends it pixel-perfectly onto the final AI generation. 100% molecular originality guaranteed.
- **LCM Safety Circuit Break**: Mathematical engine interlock that perfectly bounds Guidance Scale under `2.0` when running Fast Drafts (LCM), making "deep-fried" crashes impossible.
- **Auto-Preset Snap System**: Toggling between Fast Draft and High Quality instantly configures all 7 hidden sliders (Steps, Guidance, Padding, Blend, Sampler) to their mathematically optimal safe zones.
- **Deep Clean Plate**: A true green-screen mode. Automatically calculates the silhouette of your entire body *after* generation to securely isolate the Try-On from your provided untouched background without clipping.
- **Mask Dilation Engine**: Custom padding parameters (-10 to +30) to surgically expand AI rendering zones and completely eradicate original clothing "ghosting."
- **Fractional Face Restoration**: Variable GFPGAN slider allowing you to seamlessly mix your original skin pores and freckles alongside AI face-symmetry enhancements.
- **Karras Optimization**: The backend now natively overrides standard rendering by substituting `DPM++ 2M Karras` to maximize micro-texture clarity at 30+ steps.

---

## 🧬 Scientific Overview: How We Fixed Virtual Try-On

Standard zero-shot image-conditioned diffusion networks (like CatVTON or OOTDiffusion) suffer from massive mathematical flaws. This repository fixes them using post-process Computer Vision and Spatial Hacks.

### 1. The "Cape" Artifact Fix (DensePose Hacking)
**The Problem:** Neural engines calculate a "Convex Hull" (a rubber band stretched around your body). If your arms are raised, the hull creates massive empty white triangles between your arms and torso. The AI fills this empty space with hallucinated fabric, creating weird "capes".
**Our Solution:** We hacked the core `cloth_masker.py` to completely eradicate the Convex Hull logic. The engine now strictly enforces **Body-Hugging DensePose Arrays**, physically banning the AI from generating fabric in the empty air between limbs.

### 2. Garment Cut Constraints (Spatial Limitations)
**The Problem:** Image-conditioned AI cannot read text prompts (e.g., you cannot type "tank top" to force it to remove sleeves).
**Our Solution:** We intercept the neural DensePose map *before* generation. If you select "Sleeveless" in our UI, the backend mathematically deletes the `big arms` and `forearms` data arrays from the canvas. The AI is physically blocked from drawing sleeves.

### 3. Deep Logo & Texture Restoration (TPS Warp)
**The Problem:** Latent Diffusion auto-encoders inherently destroy high-frequency text, logos, and sharp graphics.
**Our Solution:** We built a custom **Thin-Plate Spline (TPS) Warp Engine** powered by OpenCV. 
1. It mathematically anchors 50+ spatial geometry points on your generated torso.
2. It warps the *original, high-res* flat garment onto the 3D curves of the generated body.
3. It performs a High-Pass Frequency Separation, blending the sharp 4K graphics of the original image with the 3D lighting/shadows of the AI generation.

---

## 📁 System Map
- `app.py`: The single-file Live Studio engine and UI architecture.
- `/Users/Shared/Models/`: Central neural vault for all isolated AI weights globally.
- `vendor/`: Core localized dependencies for CatVTON and Detectron2 isolated from upstream pipeline regressions.

## 🛠️ Advanced Operations
- **Fast Draft (8 Steps)**: Will execute instantly, but is restricted to testing physical clothing fit and proportion. (Ignore the harmless Diffusers console warnings).
- **High Quality (30+ Steps)**: Triggers the `DPM++ 2M Karras` scheduler and unlocks deep texture unsharp masking and Surgical Head features.

---
*Developed with 🛡️ by Antigravity*



## 📸 Image Gallery & Examples

### Perfect Racing Suit Alignment (TPS Warp + Cut Constraints)
<div align="center">
  <img src="images/test_leather_suit.png" alt="Leather Racing Suit Try-On" width="400">
</div>

*The custom TPS Warp mathematically anchors the Castrol and Honda logos to the generated body geometry, completely preserving them from Latent Diffusion destruction. The 'shorts' length constraint was engaged to perfectly map the pants.*

### Everyday Wear
<div align="center">
  <img src="images/test_theroad_girl.png" alt="Casual Try-On" width="400">
</div>

### Original Assets
* **Original Garment:** `images/garment_example.png`
* **Original Person:** `images/person_example.png`
