"""
Local Virtual Try-On - http://127.0.0.1:7860
Consolidated and cleaned version for /Users/Shared/Projects/try-on
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
import threading
from pathlib import Path
from typing import Any

import gradio as gr

# ── Apple Silicon Optimization ────────────────────────────────────────────────
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


import torch
import logging
import warnings

# Silence library noise internally
logging.getLogger("diffusers").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=UserWarning, module="torchvision")
warnings.filterwarnings("ignore", category=UserWarning, module="torch.functional")

import torchvision
import torchvision.transforms.functional as F_v
if not hasattr(torchvision.transforms, "functional_tensor"):
    sys.modules["torchvision.transforms.functional_tensor"] = sys.modules.get("torchvision.transforms.functional", F_v)


# ── Paths ───────────────────────────────────────────────────────────────────
_ROOT         = Path(__file__).resolve().parent
_VENDOR_ROOT  = _ROOT / "vendor"
_CATVTON_ROOT = _VENDOR_ROOT / "CatVTON"
_MODELS_ROOT  = _ROOT / "models"

# Internal sub-paths used by the package
_D2_ROOT      = _CATVTON_ROOT / "model" / "SCHP" / "mhp_extension" / "detectron2"
_DP_ROOT      = _D2_ROOT / "projects" / "DensePose"
_MODELS_CAT   = _MODELS_ROOT / "catvton" / "zhengchong_CatVTON"
_MODELS_SD    = _MODELS_ROOT / "sd-inpainting"
_MODELS_VAE   = _MODELS_ROOT / "catvton" / "sd_vae_ft_mse"
_LORA_LCM     = _MODELS_ROOT / "lcm_lora" / "pytorch_lora_weights.safetensors"
_MODELS_UP    = _MODELS_ROOT / "upscalers"

# ── Bootstrap detectron2 / DensePose ──────────────────────────────────────────
for _p in (_D2_ROOT, _DP_ROOT):
    _s = str(_p)
    if _p.is_dir() and _s not in sys.path:
        sys.path.insert(0, _s)

def _load_catvton_package(pkg_name: str = "catvton") -> Any:
    """
    Load CatVTON as a proper Python package.
    Modified from tryon_local to be more robust.
    """
    if pkg_name in sys.modules:
        return sys.modules[pkg_name]

    from importlib.machinery import ModuleSpec

    def _register(name: str, directory: Path) -> tuple[Any, Any]:
        if name in sys.modules:
            return sys.modules[name], None
        init = directory / "__init__.py"
        if init.exists():
            spec = importlib.util.spec_from_file_location(
                name, str(init),
                submodule_search_locations=[str(directory)],
            )
        else:
            spec = ModuleSpec(name, loader=None, origin=None)
            spec.submodule_search_locations = [str(directory)]
        mod = importlib.util.module_from_spec(spec)
        mod.__path__ = [str(directory)]
        mod.__package__ = name
        sys.modules[name] = mod
        return mod, spec

    # Register order matters for relative imports
    root_mod, root_spec = _register(pkg_name, _CATVTON_ROOT)
    sub_mods: list[tuple[Any, Any]] = []
    for sub in ("model", "model.SCHP", "model.DensePose"):
        parts = sub.split(".")
        sub_dir = _CATVTON_ROOT.joinpath(*parts)
        if sub_dir.exists():
            mod, spec = _register(f"{pkg_name}.{sub}", sub_dir)
            sub_mods.append((mod, spec))

    if root_spec and root_spec.loader:
        root_spec.loader.exec_module(root_mod)

    for mod, spec in sub_mods:
        if spec and spec.loader:
            try:
                spec.loader.exec_module(mod)
            except Exception as e:
                print(f"[warning] Failed to exec {mod.__name__}: {e}")

    return sys.modules[pkg_name]

# ── Global Model State ────────────────────────────────────────────────────────
_LOCK    = threading.Lock()
_PIPE    = None
_MASKER  = None
_ERROR   = None
_CAT_PKG = None
_UPSCALER = None
_FACE_ENHANCER = None
_READY   = threading.Event()

def _load_models():
    global _PIPE, _MASKER, _ERROR, _CAT_PKG, _READY, _UPSCALER, _FACE_ENHANCER
    import torch
    
    try:
        print("[try-on] Bootstrapping CatVTON package...")
        _CAT_PKG = _load_catvton_package()

        from catvton.model.cloth_masker import AutoMasker
        from catvton.model.pipeline import CatVTONPipeline

        # Hardware selection
        has_mps = hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
        pipe_device = "mps" if has_mps else "cpu"
        # Use MPS for masking if available to speed up DensePose/SCHP
        mask_device = "mps" if has_mps else "cpu"

        print(f"[try-on] Loading AutoMasker on {mask_device}...")
        masker = AutoMasker(
            densepose_ckpt=str(_MODELS_CAT / "DensePose"),
            schp_ckpt=str(_MODELS_CAT / "SCHP"),
            device=mask_device,
        )

        print(f"[try-on] Loading CatVTON pipeline on {pipe_device}...")
        # Enforce VAE path for absolute offline safety
        os.environ["SMF_CATVTON_VAE_PATH"] = str(_MODELS_VAE)
        
        pipe = CatVTONPipeline(
            base_ckpt=str(_MODELS_SD),
            attn_ckpt=str(_MODELS_CAT),
            attn_ckpt_version="mix",
            weight_dtype=torch.float16 if pipe_device == "mps" else torch.float32,
            device=pipe_device,
            use_tf32=True,
            local_files_only=True,
            use_safetensors=False,
        )

        with _LOCK:
            _PIPE = pipe
            _MASKER = masker
            
            # Load LCM LoRA booster using modern PEFT logic
            try:
                # Modern LoRA handshake to eliminate FutureWarning
                # Enable progress bar by not disabling it
                from diffusers.utils import logging as diffusers_logging
                diffusers_logging.set_verbosity_info() 
                _PIPE.unet.load_lora_adapter(str(_LORA_LCM), adapter_name="lcm", prefix=None)
            except Exception:
                pass

            # Load Enhancers (Optional)
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=UserWarning)
                    from realesrgan import RealESRGANer
                    from gfpgan import GFPGANer
                    
                    model_path = _MODELS_UP / "RealESRGAN_x4plus.pth"
                    if model_path.exists():
                        from basicsr.archs.rrdbnet_arch import RRDBNet
                        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
                        _UPSCALER = RealESRGANer(scale=4, model_path=str(model_path), model=model, tile=400, tile_pad=10, pre_pad=0, half=True, device=pipe_device)
                    
                    face_path = _MODELS_UP / "GFPGANv1.3.pth"
                    if face_path.exists():
                        _FACE_ENHANCER = GFPGANer(model_path=str(face_path), upscale=1, arch='clean', channel_multiplier=2, device=pipe_device)
            except Exception:
                pass
            
        _READY.set()
        print(f"[try-on] \u2713 Ready | Backend: {pipe_device.upper()}")
        
    except Exception as exc:
        import traceback
        _ERROR = f"{exc}\n{traceback.format_exc()}"
        _READY.set()
        print(f"[try-on] Load failed: {exc}")

def _inference(person_img, cloth_img, category, resolution, num_steps, guidance, seed, show_mask, mask_blur, detail_boost, face_enhance, progress=gr.Progress()):
    import torch
    from PIL import Image
    from diffusers.image_processor import VaeImageProcessor

    if not _READY.is_set():
        return None, None, "\u231b Models loading... please wait."
    if _ERROR:
        return None, None, f"\u274c Error: {_ERROR}"
    if person_img is None or cloth_img is None:
        return None, None, "Please upload both images."

    # Preprocessing
    resize_and_crop = _CAT_PKG.resize_and_crop
    resize_and_padding = _CAT_PKG.resize_and_padding

    if not isinstance(person_img, Image.Image):
        person_img = Image.fromarray(person_img)
    if not isinstance(cloth_img, Image.Image):
        cloth_img = Image.fromarray(cloth_img)

    # Resolution handling for speed
    target_size = (384, 512) if resolution == "Fast (Draft)" else (768, 1024)
    person = resize_and_crop(person_img.convert("RGB"), target_size)
    cloth = resize_and_padding(cloth_img.convert("RGB"), target_size)
    
    # 1. Masking
    t_start = time.monotonic()
    progress(0, desc="Segmenting body...")
    mask_result = _MASKER(person, category)
    mask_pil = mask_result["mask"]
    mask_pil = VaeImageProcessor(
        vae_scale_factor=8, do_normalize=False,
        do_binarize=True, do_convert_grayscale=True,
    ).blur(mask_pil, blur_factor=int(mask_blur))
    t_mask = time.monotonic() - t_start
    
    # 2. Diffusion
    progress(0.2, desc=f"Masking done ({t_mask:.1f}s). Starting diffusion...")
    
    # Stability: Clear cache and synchronize for Apple Silicon
    if torch.backends.mps.is_available():
        import torch.mps
        torch.mps.empty_cache()
        torch.mps.synchronize()
        
    gen = torch.Generator(device="mps").manual_seed(int(seed))
    print(f"[try-on] Run: res={resolution}, steps={num_steps}, guidance={guidance}, category={category}")
    
    # Speed Optimization: Switch Scheduler for low steps (Surgically Silenced)
    from diffusers import LCMScheduler, EulerAncestralDiscreteScheduler
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if int(num_steps) < 10:
            _PIPE.noise_scheduler = LCMScheduler.from_config(_PIPE.noise_scheduler.config)
            actual_guidance = float(guidance)
        else:
            _PIPE.noise_scheduler = EulerAncestralDiscreteScheduler.from_config(_PIPE.noise_scheduler.config)
            actual_guidance = float(guidance)

    # Run pipeline
    t_diff_start = time.monotonic()
    
    # Stability: Enable attention slicing to save VRAM
    if hasattr(_PIPE, "enable_attention_slicing"):
        _PIPE.enable_attention_slicing()

    try:
        result = _PIPE(
            image=person, 
            condition_image=cloth, 
            mask=mask_pil,
            num_inference_steps=int(num_steps),
            guidance_scale=actual_guidance,
            generator=gen,
        )
    except Exception as e:
        print(f"[ERROR] Diffusion failed: {e}")
        raise e

    t_diff = time.monotonic() - t_diff_start
    
    result_img = result.images[0] if hasattr(result, "images") else result[0]
    
    # 3. High-Fidelity Finishing
    if resolution == "High Quality" and (detail_boost > 0 or face_enhance):
        import numpy as np
        progress(0.9, desc="Polishing result (Upscale & Restore)...")
        img_np = np.array(result_img)
        
        # Face Restoration
        if face_enhance and _FACE_ENHANCER:
            _, _, restored_img = _FACE_ENHANCER.enhance(img_np, has_aligned=False, only_center_face=False, paste_back=True)
            img_np = restored_img
            
        # Optional Sharpening for patterns
        if detail_boost > 0:
            from PIL import ImageFilter
            result_img = Image.fromarray(img_np).filter(ImageFilter.UnsharpMask(radius=2, percent=int(detail_boost * 100), threshold=3))
            img_np = np.array(result_img)
            
        result_img = Image.fromarray(img_np)

    mask_out = mask_pil if show_mask else None
    return result_img, mask_out, f"\u2705 Done! [Mask: {t_mask:.1f}s | Diff: {t_diff:.1f}s | Total: {t_mask+t_diff:.1f}s]"

# ── Gradio UI ─────────────────────────────────────────────────────────────────
def build_ui():
    with gr.Blocks(title="Try-On Local") as demo:
        gr.Markdown("# Lightweight Local Virtual Try-On")
        
        with gr.Row():
            with gr.Column():
                person_in = gr.Image(label="Person Photo", type="numpy")
                cloth_in  = gr.Image(label="Garment Image", type="numpy")
                category  = gr.Radio(["upper", "lower", "dresses"], value="upper", label="Category")
                resolution = gr.Radio(["Fast (Draft)", "High Quality"], value="Fast (Draft)", label="Resolution")
                
            with gr.Column():
                with gr.Row():
                    steps = gr.Slider(4, 50, value=6, step=1, label="Steps (4-8 for instant, 15+ for quality)")
                    guidance = gr.Slider(1.0, 5.0, value=1.5, step=0.1, label="Guidance")
                seed = gr.Number(value=42, label="Seed")
                with gr.Accordion("Advanced High-Fidelity Settings", open=True):
                    mask_blur = gr.Slider(0, 15, value=5, step=1, label="Mask Blending (Lower = Sharper Logos)")
                    detail_boost = gr.Slider(0.0, 1.0, value=0.4, step=0.1, label="Logo/Pattern Detail Boost")
                    face_enhance = gr.Checkbox(label="Face Restoration (GFPGAN)", value=True)
                    show_mask = gr.Checkbox(label="Show Masking Step", value=False)

                run_btn = gr.Button("Generate Try-On", variant="primary")
                
                result_out = gr.Image(label="Result")
                mask_out   = gr.Image(label="Mask", visible=False)
                status_out = gr.Textbox(label="Status", interactive=False)

        show_mask.change(lambda v: gr.update(visible=v), show_mask, mask_out)
        run_btn.click(
            fn=_inference,
            inputs=[person_in, cloth_in, category, resolution, steps, guidance, seed, show_mask, mask_blur, detail_boost, face_enhance],
            outputs=[result_out, mask_out, status_out]
        )

    return demo

if __name__ == "__main__":
    threading.Thread(target=_load_models, daemon=True).start()
    demo = build_ui()
    demo.launch(
        server_name="127.0.0.1", 
        server_port=7860,
        theme=gr.themes.Soft()
    )
