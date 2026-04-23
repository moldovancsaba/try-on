# 👕 High-Fidelity Virtual Try-On (Hybrid Neural-Math Engine)

**Zero-Shot Diffusion | TPS Geometric Warp | DensePose Cut Constraints**

This repository is a significantly modified, production-grade evolution of the open-source Virtual Try-On paradigm. It solves the fundamental limitations of latent diffusion (hallucinations, cape artifacts, text destruction) by wrapping the core neural engine in a **Custom Mathematical Architecture**. 

Designed to push standard hardware (Apple Silicon MPS / NVIDIA CUDA) to its absolute limits without requiring massive $3,000 graphics cards.

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

## 🚀 Quick Start (Installation)

We have provided a robust bash script that automatically constructs the Centralized Hub and uses `huggingface-cli` to securely download the hundreds of megabytes of required weights directly into it.

1. **Clone the Repository & Run the Installer:**
   ```bash
   chmod +x install.sh run.sh
   ./install.sh
   ```
   *(The script will create `.venv311`, install PyTorch/Diffusers/OpenCV, and download GFPGAN, LCM, CatVTON, and InsightFace weights into `/Users/Shared/Models/`)*

2. **Launch the Studio:**
   ```bash
   ./run.sh
   ```

---

## 🧬 Scientific Overview: How We Fixed Virtual Try-On

Standard zero-shot image-conditioned diffusion networks (like CatVTON or OOTDiffusion) suffer from three massive mathematical flaws. This repository fixes all of them using post-process Computer Vision and Spatial Hacks.

### 1. The "Cape" Artifact Fix (DensePose Hacking)
**The Problem:** Neural engines calculate a "Convex Hull" (a rubber band stretched around your body) to determine where to draw clothing. If your arms are raised, the hull creates massive empty white triangles between your arms and torso. The AI fills this empty space with hallucinated fabric, creating weird "capes" or webbed sleeves.
**Our Solution:** We hacked the core `cloth_masker.py` to completely eradicate the Convex Hull logic. The engine now strictly enforces **Body-Hugging DensePose Arrays**, physically banning the AI from generating fabric in the empty air between limbs.

### 2. Garment Cut Constraints (Spatial Limitations)
**The Problem:** Image-conditioned AI cannot read text prompts (e.g., you cannot type "tank top" to force it to remove sleeves).
**Our Solution:** We intercept the neural DensePose map *before* generation. If you select "Sleeveless" in our UI, the backend mathematically deletes the `big arms` and `forearms` data arrays from the canvas. The AI is physically blocked from drawing sleeves, forcing it to generate a tank-top cut. 

### 3. Deep Logo & Texture Restoration (TPS Warp)
**The Problem:** Latent Diffusion auto-encoders inherently destroy high-frequency text, logos, and sharp graphics, turning them into unreadable "AI alien text".
**Our Solution:** We built a custom **Thin-Plate Spline (TPS) Warp Engine** powered by OpenCV. 
1. It uses ORB feature-matching to mathematically anchor 50+ spatial geometry points on your generated torso.
2. It mathematically warps the *original, high-res* flat garment onto the 3D curves of the generated body.
3. It performs a High-Pass Frequency Separation, blending the sharp 4K graphics of the original image with the 3D lighting/shadows of the AI generation. Logos remain pixel-perfect.

### 4. Dynamic Clean Plate Compositing
**The Problem:** Pasting the generated person back onto their original background usually results in chopped-off limbs if the new clothing is bulkier than the old clothing.
**Our Solution:** The engine halts at 96% progress and runs the `AutoMasker` a *second time* on the newly generated AI image. It calculates a brand-new, wider silhouette that perfectly hugs the bulky clothing, and uses that new silhouette as the alpha-mask for the final background composite. 

### 5. Surgical Head Paste
Because FaceID LoRAs inherently degrade image quality, we calculate an exact Alpha Mask of your original face, hair, and sunglasses. We feather the edges and mathematically composite your *exact original pixels* over the AI generation. 100% original identity guaranteed.

---
*Architected and engineered for the Open Source Community.*
