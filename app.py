"""
Local Virtual Try-On - http://127.0.0.1:7860
Consolidated and cleaned version for /Users/Shared/Projects/try-on
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import time
import threading
from pathlib import Path
from typing import Any

import gradio as gr

# ── Apple Silicon & Environment Optimization ──────────────────────────────────
_MODELS_ROOT_STR = os.environ.get("TRYON_MODELS_ROOT", "/Users/Shared/Models")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
# Centralize HuggingFace Cache and enforce Absolute Offline Mode
os.environ["HF_HOME"] = str(Path(_MODELS_ROOT_STR).expanduser() / ".cache" / "huggingface")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# Silence Verbose Engine Logs
import logging
logging.getLogger("onnxruntime").setLevel(logging.ERROR)


import torch
import logging
import warnings

if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
    os.environ.setdefault("SMF_CATVTON_USE_MPS", "1")

# Silence higher-level library noise
logging.getLogger("diffusers").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("torch.distributed.elastic.multiprocessing.redirects").setLevel(logging.ERROR)
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
_MODELS_ROOT  = Path(_MODELS_ROOT_STR).expanduser().resolve()

# Internal sub-paths used by the package (Redirected to Master Standard Vault)
_D2_ROOT      = _CATVTON_ROOT / "model" / "SCHP" / "mhp_extension" / "detectron2"
_DP_ROOT      = _D2_ROOT / "projects" / "DensePose"
_MODELS_CAT   = _MODELS_ROOT / "processors" / "catvton-segmentation"
_MODELS_SD    = _MODELS_ROOT / "checkpoints" / "sd15-inpainting"
_MODELS_VAE   = _MODELS_ROOT / "vae" / "sd15-vae-ft-mse"
_MODELS_GF    = _MODELS_ROOT / "processors" / "face-restoration"
_GFPGAN_PRIMARY = _MODELS_GF / "GFPGANv1.4.pth"
_GFPGAN_LEGACY = _MODELS_ROOT / "processors" / "upscalers" / "GFPGANv1.3.pth"
_GFPGAN_RUNTIME_DIR = _ROOT / "gfpgan" / "weights"
_GFPGAN_RUNTIME_SUPPORT = {
    "detection_Resnet50_Final.pth": _MODELS_GF / "detection_Resnet50_Final.pth",
    "parsing_parsenet.pth": _MODELS_GF / "parsing_parsenet.pth",
}


def _has_mps() -> bool:
    return hasattr(torch.backends, "mps") and torch.backends.mps.is_available()


def _preferred_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if _has_mps():
        return "mps"
    return "cpu"


def _require_path(path: Path, *, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing required {label}: {path}. "
            "Run ./install.sh to download the offline dependencies."
        )


def _resolve_gfpgan_checkpoint() -> Path:
    if _GFPGAN_PRIMARY.exists():
        return _GFPGAN_PRIMARY
    if _GFPGAN_LEGACY.exists():
        return _GFPGAN_LEGACY
    raise FileNotFoundError(
        "Missing GFPGAN checkpoint. "
        f"Checked {_GFPGAN_PRIMARY} and {_GFPGAN_LEGACY}. "
        "Run ./install.sh to download the offline dependencies."
    )


def _seed_gfpgan_runtime_weights() -> None:
    _GFPGAN_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    for filename, source_path in _GFPGAN_RUNTIME_SUPPORT.items():
        _require_path(source_path, label=f"GFPGAN support weight {filename}")
        target_path = _GFPGAN_RUNTIME_DIR / filename
        if not target_path.exists():
            shutil.copy(source_path, target_path)

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
_FACE_ENHANCER = None
_LOADED_VAE_TYPE = "hf" 
_GFPGAN_READY = False
_GFPGAN_ERROR = None
_READY   = threading.Event()

def _load_models():
    global _PIPE, _MASKER, _ERROR, _CAT_PKG, _READY, _FACE_ENHANCER
    global _GFPGAN_READY, _GFPGAN_ERROR
    import torch
    
    try:
        print("[try-on] Bootstrapping CatVTON package...")
        _CAT_PKG = _load_catvton_package()

        from catvton.model.cloth_masker import AutoMasker
        from catvton.model.pipeline import CatVTONPipeline

        _require_path(_MODELS_CAT / "DensePose", label="DensePose checkpoint")
        _require_path(_MODELS_CAT / "SCHP", label="SCHP checkpoint")
        _require_path(_MODELS_SD, label="Stable Diffusion inpainting checkpoint")
        _require_path(_MODELS_VAE, label="VAE checkpoint")

        # Hardware selection
        pipe_device = _preferred_device()
        mask_device = pipe_device

        print(f"[try-on] Loading AutoMasker on {mask_device}...")
        masker = AutoMasker(
            densepose_ckpt=str(_MODELS_CAT / "DensePose"),
            schp_ckpt=str(_MODELS_CAT / "SCHP"),
            device=mask_device,
        )

        print(f"[try-on] Loading CatVTON pipeline on {pipe_device}...")
        # Enforce VAE path for absolute offline safety
        os.environ["SMF_CATVTON_VAE_PATH"] = str(_MODELS_VAE)
        
        # Precision VAE Handshake: Use float32 for VAE on MPS to prevent color drift
        # Even if the UNet is float16, the VAE is safer in float32 for color accuracy
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

            # Load GFPGAN face restoration when fully available.
            _GFPGAN_READY = False
            _GFPGAN_ERROR = None
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=UserWarning)
                    from gfpgan import GFPGANer
                    _seed_gfpgan_runtime_weights()
                    face_path = _resolve_gfpgan_checkpoint()
                    _FACE_ENHANCER = GFPGANer(
                        model_path=str(face_path),
                        upscale=1,
                        arch="clean",
                        channel_multiplier=2,
                        device=pipe_device,
                    )
                    _GFPGAN_READY = True
            except Exception as exc:
                _GFPGAN_ERROR = str(exc)
                _FACE_ENHANCER = None
                print(f"[warning] GFPGAN unavailable: {exc}")

        _READY.set()
        print(f"[try-on] \u2713 Ready | Backend: {pipe_device.upper()}")
        
    except Exception as exc:
        import traceback
        _ERROR = f"{exc}\n{traceback.format_exc()}"
        _READY.set()
        print(f"[try-on] Load failed: {exc}")

def _inference(person_img, cloth_img, category, sleeve_length, pant_length, resolution, num_steps, guidance, seed, show_mask, mask_sharpness, mask_padding, detail_boost, face_restore_strength, preserve_head, lock_seed, use_vae_hf, sampler_name, bg_plate, composite_strength, enable_deep_texture, warp_strength, progress=gr.Progress()):
    import torch
    import random
    import json
    from PIL import Image
    from diffusers.image_processor import VaeImageProcessor
    from diffusers import AutoencoderKL
    from catvton.utils import numpy_to_pil

    if not _READY.is_set():
        yield None, None, "⌛ Models loading... please wait.", gr.update(), gr.update()
        return
    if _ERROR:
        yield None, None, f"❌ Error: {_ERROR}", gr.update(), gr.update()
        return
    if person_img is None or cloth_img is None:
        yield None, None, "Please upload both images.", gr.update(), gr.update()
        return

    # 💾 Save Last Settings
    try:
        settings = {
            "category": category, "sleeve_length": sleeve_length, "pant_length": pant_length,
            "resolution": resolution, "steps": num_steps, "guidance": guidance,
            "seed": seed, "show_mask": show_mask, "mask_sharpness": mask_sharpness, "mask_padding": mask_padding,
            "detail_boost": detail_boost, "face_restore_strength": face_restore_strength, "preserve_head": preserve_head, 
            "lock_seed": lock_seed, "use_vae_hf": use_vae_hf,
            "sampler_name": sampler_name, "composite_strength": composite_strength,
            "enable_deep_texture": enable_deep_texture, "warp_strength": warp_strength
        }
        with open(_MODELS_ROOT / "settings.json", "w") as f:
            json.dump(settings, f)
    except Exception as e:
        print(f"[warning] Failed to save settings: {e}")

    # 🎭 Neural VAE Hot-Swap & Identity State
    global _LOADED_VAE_TYPE
    requested_vae = "hf" if use_vae_hf else "standard"
    if _LOADED_VAE_TYPE != requested_vae:
        progress(0, desc=f"Hot-swapping to {requested_vae} VAE...")
        new_vae_path = str(_MODELS_VAE) if use_vae_hf else str(_MODELS_SD / "vae")
        with _LOCK:
            _PIPE.vae = AutoencoderKL.from_pretrained(
                new_vae_path, 
                local_files_only=True, 
                use_safetensors=False
            ).to(_PIPE.device, dtype=_PIPE.vae_dtype)
            _LOADED_VAE_TYPE = requested_vae
        print(f"[try-on] VAE Hot-Swapped to {requested_vae}")

    # 🔒 Button Lockdown & Mining
    actual_seed = int(seed)
    if not lock_seed:
        actual_seed = random.randint(0, 2147483647)
        yield None, None, f"🎲 Mining Seed... {actual_seed}", gr.update(value=actual_seed), gr.update(interactive=False, value="⌛ Generating...")
    else:
        yield None, None, f"🚀 Launching...", gr.update(), gr.update(interactive=False, value="⌛ Generating...")

    # Preprocessing
    resize_and_crop = _CAT_PKG.resize_and_crop
    resize_and_padding = _CAT_PKG.resize_and_padding

    if not isinstance(person_img, Image.Image):
        person_img = Image.fromarray(person_img)
    if not isinstance(cloth_img, Image.Image):
        cloth_img = Image.fromarray(cloth_img)

    # Standalone build uses the stable high-quality render path only.
    target_size = (768, 1024)
    person = resize_and_crop(person_img.convert("RGB"), target_size)
    cloth = resize_and_padding(cloth_img.convert("RGB"), target_size)
    
    # Masking logic: Invert sharpness to blur (15 sharpness = 0 blur)
    actual_blur = 15 - int(mask_sharpness)
    t_start = time.monotonic()
    progress(0, desc="Segmenting body...")
    
    # AutoMasker Mapping
    category_map = {
        "Upper (T-Shirts, Hoodies)": "upper",
        "Lower (Jeans, Shorts, Skirts)": "lower",
        "Dresses (Full-Body, Suits, Rompers)": "overall",
        "Outerwear (Jackets, Coats)": "outer"
    }
    automask_category = category_map.get(category, "upper")
    mask_result = _MASKER(person, automask_category, sleeve_length=sleeve_length, pant_length=pant_length)
    mask_pil = mask_result["mask"]

    # --- Identity Map & Full Silhouette Extraction ---
    import numpy as np
    schp_lip = np.array(mask_result["schp_lip"])
    # 0 = Background in LIP mapping, everything else is the person
    full_body_np = (schp_lip > 0)
    full_body_mask_pil = Image.fromarray((full_body_np * 255).astype(np.uint8)).convert("L")
    
    # --- Head Processing for Surgical Paste ---
    head_mask_pil = None
    if preserve_head:
        schp_atr = np.array(mask_result["schp_atr"])
        
        # Face=13, Hair=2, Hat=1, Sunglasses=4 (LIP)
        lip_head_map = [1, 2, 4, 13]
        # Face=11, Hair=2, Hat=1, Sunglasses=3 (ATR)
        atr_head_map = [1, 2, 3, 11]
        
        head_mask_np = np.zeros_like(schp_lip, dtype=bool)
        for idx in lip_head_map:
            head_mask_np |= (schp_lip == idx)
        for idx in atr_head_map:
            head_mask_np |= (schp_atr == idx)
            
        head_mask_pil = Image.fromarray((head_mask_np * 255).astype(np.uint8)).convert("L")
    
    # Advanced Mask Padding (Expand/Erode Silhouette)
    from PIL import ImageFilter
    if mask_padding > 0:
        mask_pil = mask_pil.filter(ImageFilter.MaxFilter(size=int(mask_padding * 2 + 1)))
    elif mask_padding < 0:
        mask_pil = mask_pil.filter(ImageFilter.MinFilter(size=int(abs(mask_padding) * 2 + 1)))

    # Fix hem V-cut artefact: expand the mask 8px downward so the composite
    # does not clip the bottom edge of the garment into a V-shape.
    _mask_arr = np.array(mask_pil.convert("L"))
    for _row in range(_mask_arr.shape[0] - 1, max(0, _mask_arr.shape[0] - 100), -1):
        if _mask_arr[_row].max() > 64:
            _bottom = _row
            _end = min(_mask_arr.shape[0], _bottom + 8)
            _mask_arr[_bottom:_end, :] = np.maximum(
                _mask_arr[_bottom:_end, :], _mask_arr[_row:_row+1, :]
            )
            break
    mask_pil = Image.fromarray(_mask_arr).convert("L")

    mask_pil = VaeImageProcessor(
        vae_scale_factor=8, do_normalize=False,
        do_binarize=True, do_convert_grayscale=True,
    ).blur(mask_pil, blur_factor=actual_blur)
    t_mask = time.monotonic() - t_start
    
    # 2. Diffusion
    progress(0.2, desc=f"Masking done ({t_mask:.1f}s). Starting diffusion...")
    
    # Stability: Clear cache and synchronize for Apple Silicon
    if _has_mps():
        import torch.mps
        torch.mps.empty_cache()
        torch.mps.synchronize()

    generator_device = str(getattr(_PIPE, "device", _preferred_device()))
    try:
        gen = torch.Generator(device=generator_device).manual_seed(actual_seed)
    except Exception:
        gen = torch.Generator(device="cpu").manual_seed(actual_seed)
    print(f"[try-on] Run: res={resolution}, steps={num_steps}, guidance={guidance}, seed={actual_seed}")
    
    # Stable scheduler selection for the standalone build.
    from diffusers import EulerAncestralDiscreteScheduler, DPMSolverMultistepScheduler, UniPCMultistepScheduler

    if resolution == "Fast (Draft)":
        yield None, None, "❌ Fast (Draft) is disabled in the standalone build. Use High Quality.", gr.update(), gr.update(interactive=True, value="Generate Try-On")
        return
    
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if sampler_name == "DPM++ 2M":
            config = _PIPE.noise_scheduler.config
            _PIPE.noise_scheduler = DPMSolverMultistepScheduler.from_config(config, use_karras_sigmas=True)
        elif sampler_name == "UniPC":
            _PIPE.noise_scheduler = UniPCMultistepScheduler.from_config(_PIPE.noise_scheduler.config)
        else:
            _PIPE.noise_scheduler = EulerAncestralDiscreteScheduler.from_config(_PIPE.noise_scheduler.config)
        actual_guidance = float(guidance)

    # Run pipeline
    t_diff_start = time.monotonic()
    
    # Stability: Enable attention slicing to save VRAM
    if hasattr(_PIPE, "enable_attention_slicing"):
        _PIPE.enable_attention_slicing()

    result_img = None
    try:
        # 🎞️ Live Studio Loop
        for i, t, latents in _PIPE(
            image=person, 
            condition_image=cloth, 
            mask=mask_pil,
            num_inference_steps=int(num_steps),
            guidance_scale=actual_guidance,
            generator=gen,
            callback_steps=4,
        ):
            if isinstance(latents, torch.Tensor):
                # Decode intermediate latents for live preview only.
                with torch.no_grad():
                    l = 1 / _PIPE.vae.config.scaling_factor * latents
                    l = l.split(l.shape[-2] // 2, dim=-2)[0]
                    preview = _PIPE.vae.decode(l.to(_PIPE.device, dtype=_PIPE.vae_dtype)).sample
                    preview = (preview / 2 + 0.5).clamp(0, 1)
                    if _PIPE.device == "mps":
                        preview = preview.float()
                    preview = preview.cpu().permute(0, 2, 3, 1).numpy()
                    preview_img = numpy_to_pil(preview)[0]
                    yield preview_img, None, f"🎞️ Building... {int((i/int(num_steps))*100)}%", gr.update(), gr.update()
            elif latents is not None:
                # Final pipeline payload may be a PIL image or list of images.
                result_img = latents

        if result_img is None and latents is not None:
            result_img = latents
        
    except Exception as e:
        print(f"[ERROR] Diffusion failed: {e}")
        yield None, None, f"❌ Diffusion failed: {e}", gr.update(), gr.update()
        return

    t_diff = time.monotonic() - t_diff_start
    
    # 3. High-Fidelity Finishing
    import numpy as np
    from PIL import ImageFilter
    
    # Always normalize result_img to a clean PIL Image
    if isinstance(result_img, list):
        result_img = result_img[0]
    img_np = np.array(result_img).squeeze()
    if img_np.dtype != np.uint8:
        img_np = (img_np * 255).astype(np.uint8) if img_np.max() <= 1.0 else img_np.astype(np.uint8)
    
    if resolution == "High Quality":
        progress(0.9, desc="Polishing result (Upscale & Restore)...")
        
        # Face Restoration with Fractional Blending
        if face_restore_strength > 0 and _FACE_ENHANCER:
            _, _, restored_img = _FACE_ENHANCER.enhance(img_np, has_aligned=False, only_center_face=False, paste_back=True)
            if face_restore_strength < 1.0:
                raw_img_pil = Image.fromarray(img_np)
                restored_pil = Image.fromarray(restored_img)
                blended_pil = Image.blend(raw_img_pil, restored_pil, alpha=face_restore_strength)
                img_np = np.array(blended_pil)
            else:
                img_np = restored_img
        elif face_restore_strength > 0:
            detail = _GFPGAN_ERROR or (
                f"Missing checkpoint: expected one of {_GFPGAN_PRIMARY} or {_GFPGAN_LEGACY}"
            )
            yield None, None, f"❌ Face restoration is unavailable. {detail}", gr.update(), gr.update(interactive=True, value="Generate Try-On")
            return
            
        # Optional Masked Sharpening for patterns
        if detail_boost > 0:
            sharpened_pil = Image.fromarray(img_np).filter(ImageFilter.UnsharpMask(radius=2, percent=int(detail_boost * 100), threshold=3))
            
            # Use the garment mask to isolate the sharpening to the cloth only
            if mask_pil is not None:
                garment_mask = mask_pil.copy().convert("L")
                garment_mask = garment_mask.filter(ImageFilter.GaussianBlur(radius=2))
                
                raw_img = Image.fromarray(img_np)
                result_img = Image.composite(sharpened_pil, raw_img, garment_mask)
            else:
                result_img = sharpened_pil
                
            img_np = np.array(result_img)
    
    result_img = Image.fromarray(img_np)
    
    # 🌀 Deep Texture & Logo Restoration (TPS Warp)
    if enable_deep_texture:
        progress(0.91, desc="Warping Original Textures...")
        from warp_repair import texture_repair_pass
        result_img = texture_repair_pass(cloth_img, result_img, mask_pil, warp_strength=warp_strength)
    
    # ❤️ Surgical Head Paste (100% Originality)
    if preserve_head and head_mask_pil is not None:
        progress(0.92, desc="Applying Surgical Head Paste...")
        head_src = person.resize(result_img.size, Image.LANCZOS) if person.size != result_img.size else person
        head_alpha = head_mask_pil.resize(result_img.size, Image.LANCZOS) if head_mask_pil.size != result_img.size else head_mask_pil
        feathered_head = head_alpha.filter(ImageFilter.GaussianBlur(radius=3))
        result_img = Image.composite(head_src, result_img, feathered_head)

    # 🏙️ Clean Plate Compositing (VFX Post-Process)
    if bg_plate is not None and composite_strength > 0:
        progress(0.95, desc="Compositing onto Clean Plate...")
        from PIL import Image, ImageOps, ImageFilter
        import numpy as np
        
        # 1. Prepare Background
        if not isinstance(bg_plate, Image.Image):
            bg_plate = Image.fromarray(bg_plate)
        bg_plate = bg_plate.convert("RGB").resize(result_img.size, Image.LANCZOS)
        
        # 2. Extract New Alpha from Generated Body Mask
        progress(0.96, desc="Extracting New Silhouette...")
        gen_mask_result = _MASKER(result_img, automask_category, sleeve_length="default", pant_length="default") 
        gen_schp_lip = np.array(gen_mask_result["schp_lip"])
        
        # 0 is background, >0 is person
        new_silhouette_np = (gen_schp_lip > 0).astype(np.uint8) * 255
        person_alpha = Image.fromarray(new_silhouette_np, mode="L")
        
        if composite_strength < 1.0:
            person_alpha = person_alpha.point(lambda p: int(p * composite_strength))
        
        # Feather the edges to avoid "chopping"
        person_alpha = person_alpha.filter(ImageFilter.GaussianBlur(radius=2))
        
        # 3. Composite (Generated result OVER original plate)
        final_composite = Image.composite(result_img, bg_plate, person_alpha)
        result_img = final_composite

    mask_out = mask_pil if show_mask else None
    yield result_img, mask_out, f"✓ Ready | Latency: {t_mask+t_diff:.1f}s", gr.update(), gr.update(interactive=True, value="Generate Try-On")

# ── Gradio UI ─────────────────────────────────────────────────────────────────
def load_settings():
    import json
    settings_file = _MODELS_ROOT / "settings.json"
    if settings_file.exists():
        try:
            with open(settings_file, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def build_ui():
    s = load_settings()

    with gr.Blocks(title="Try-On Local") as demo:
        gr.HTML(get_navbar("try-on"))
        gr.Markdown("# Lightweight Local Virtual Try-On")
        
        with gr.Row():
            with gr.Column():
                person_in = gr.Image(label="Person Photo", type="numpy")
                cloth_in  = gr.Image(label="Garment Image", type="numpy")
                # Handle legacy config values gracefully to prevent Gradio warnings
                legacy_map = {"upper": "Upper (T-Shirts, Hoodies)", "lower": "Lower (Jeans, Shorts, Skirts)", "dresses": "Dresses (Full-Body, Suits, Rompers)", "outer": "Outerwear (Jackets, Coats)"}
                saved_cat = s.get("category", "Upper (T-Shirts, Hoodies)")
                saved_cat = legacy_map.get(saved_cat, saved_cat)
                
                category  = gr.Dropdown([
                    "Upper (T-Shirts, Hoodies)", 
                    "Lower (Jeans, Shorts, Skirts)", 
                    "Dresses (Full-Body, Suits, Rompers)", 
                    "Outerwear (Jackets, Coats)"
                ], value=saved_cat, label="Garment Category")
                with gr.Accordion("Garment Cut Constraints (Optional)", open=False):
                    sleeve_length = gr.Radio(["default", "short_sleeve", "sleeveless"], value=s.get("sleeve_length", "default"), label="Sleeve Length Limit")
                    pant_length = gr.Radio(["default", "shorts"], value=s.get("pant_length", "default"), label="Pant Length Limit")
                resolution = gr.Radio(["High Quality"], value="High Quality", label="Resolution")
                bg_plate = gr.Image(label="Background Plate (Optional)", type="numpy")
            with gr.Column():
                with gr.Group():
                    steps = gr.Slider(4, 50, value=s.get("steps", 20), step=1, label="Steps (Slide Right for Quality)")
                    guidance = gr.Slider(1.0, 5.0, value=s.get("guidance", 3.5), step=0.1, label="Guidance (3.5 is Standard)")
                    mask_sharpness = gr.Slider(0, 15, value=s.get("mask_sharpness", 12), step=1, label="Logo & Pattern Sharpness (Slide Right for Quality)")
                    mask_padding = gr.Slider(-10, 30, value=s.get("mask_padding", 5), step=1, label="Mask Padding (Expand Silhouette)")
                    detail_boost = gr.Slider(0.0, 1.0, value=s.get("detail_boost", 0.4), step=0.1, label="Logo/Pattern Detail Boost")
                    composite_strength = gr.Slider(0.0, 1.0, value=s.get("composite_strength", 0.0), step=0.1, label="Clean Plate Blend (0 = OFF)")

                    
                with gr.Row():
                    seed = gr.Number(value=s.get("seed", 42), label="Seed", precision=0, scale=4, container=False)
                    btn_42   = gr.Button("42", size="sm", min_width=60, scale=0)
                    btn_1337 = gr.Button("1337", size="sm", min_width=60, scale=0)
                    lock_seed = gr.Checkbox(label="🔒 Lock", value=s.get("lock_seed", False), scale=0, container=False)
                
                with gr.Accordion("Options", open=True):
                    preserve_head = gr.Checkbox(label="Preserve Original Head ♥️ (Literal Pixel Paste)", value=s.get("preserve_head", True))
                    use_vae_hf = gr.Checkbox(label="High-Fidelity VAE (ft-mse)", value=s.get("use_vae_hf", True))
                    face_restore_strength = gr.Slider(0.0, 1.0, value=s.get("face_restore_strength", 1.0), step=0.1, label="Face Restore Blend (GFPGAN)")
                    sampler = gr.Dropdown(["Euler A", "DPM++ 2M", "UniPC"], value=s.get("sampler_name", "Euler A"), label="High Quality Sampler")
                    enable_deep_texture = gr.Checkbox(label="Deep Logo & Texture Restoration (TPS Warp)", value=s.get("enable_deep_texture", False))
                    warp_strength = gr.Slider(0.0, 1.0, value=s.get("warp_strength", 1.0), step=0.1, label="Texture Warp Blend Force")
                    show_mask = gr.Checkbox(label="Show Masking Step (Debug)", value=s.get("show_mask", False))

                run_btn = gr.Button("Generate Try-On", variant="primary")
                status_out = gr.Textbox(label="Status", interactive=False, container=False)
                
                result_out = gr.Image(label="Result", interactive=False)
                mask_out   = gr.Image(label="Mask", visible=False)

        # 🎲 Seed Snap Logic
        btn_42.click(fn=lambda: (42, True), outputs=[seed, lock_seed])
        btn_1337.click(fn=lambda: (1337, True), outputs=[seed, lock_seed])

        # 🎛️ Auto-Preset: Snap sliders to optimal values per mode
        def apply_preset(_res):
            return (
                gr.update(value=30),   # steps
                gr.update(value=3.5),  # guidance (full Euler sweet spot)
                gr.update(value=12),   # mask_sharpness
                gr.update(value=5),    # mask_padding
                gr.update(value=0.0),  # detail_boost off for first-fit validation
                gr.update(value=0.0),  # face_restore_strength off for first-fit validation
                gr.update(value=False),  # preserve_head off until fit is confirmed
            )
        
        resolution.change(
            fn=apply_preset,
            inputs=[resolution],
            outputs=[steps, guidance, mask_sharpness, mask_padding, detail_boost, face_restore_strength, preserve_head],
        )

        show_mask.change(lambda v: gr.update(visible=v), show_mask, mask_out)
        run_btn.click(
            fn=_inference,
            inputs=[
                person_in, cloth_in, category, sleeve_length, pant_length, resolution, steps, guidance, seed, 
                show_mask, mask_sharpness, mask_padding, detail_boost, face_restore_strength, preserve_head, lock_seed, use_vae_hf,
                sampler, bg_plate, composite_strength, enable_deep_texture, warp_strength
            ],
            outputs=[result_out, mask_out, status_out, seed, run_btn],
            show_progress="hidden"
        )

    return demo

from fastapi import FastAPI, HTTPException, Request, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import shutil
import json
from pydantic import BaseModel, Field

fastapi_app = FastAPI()

# Setup static files for the studio
import os
STUDIO_DIR = _ROOT / 'studio_tools'
PACKAGES_DIR = os.path.join(STUDIO_DIR, 'packages')
MAPS_DIR = os.path.join(STUDIO_DIR, 'master_maps')
UPLOADS_DIR = os.path.join(STUDIO_DIR, 'uploads')
TEMPLATES_DIR = os.path.join(STUDIO_DIR, 'templates')

STATIC_DIR = os.path.join(STUDIO_DIR, 'static')

os.makedirs(PACKAGES_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

fastapi_app.mount("/maps", StaticFiles(directory=MAPS_DIR), name="maps")
fastapi_app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
fastapi_app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def get_navbar(active="try-on"):
    with open(os.path.join(TEMPLATES_DIR, "navbar.html"), "r") as f:
        html = f.read()
    # Simple manual replacement for Gradio since we aren't using Jinja here
    html = html.replace("{{ 'active' if active == 'try-on' else '' }}", "active" if active == "try-on" else "")
    html = html.replace("{{ 'active' if active == 'set-garment' else '' }}", "active" if active == "set-garment" else "")
    html = html.replace("{{ 'active' if active == 'garments' else '' }}", "active" if active == "garments" else "")
    return html

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=TEMPLATES_DIR)

@fastapi_app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse(request=request, name="landing.html", context={"active": ""})

@fastapi_app.get("/set-garment", response_class=HTMLResponse)
async def setup_studio(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"active": "set-garment"})

@fastapi_app.get("/garments", response_class=HTMLResponse)
async def library_page(request: Request):
    packages = []
    if os.path.exists(PACKAGES_DIR):
        packages = [p for p in os.listdir(PACKAGES_DIR) if os.path.isdir(os.path.join(PACKAGES_DIR, p))]
    return templates.TemplateResponse(request=request, name="library.html", context={"packages": packages, "active": "garments"})

@fastapi_app.post("/upload_garment")
async def upload_garment(file: UploadFile = File(...)):
    filename = file.filename
    save_path = os.path.join(UPLOADS_DIR, filename)
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return JSONResponse({'url': f'/uploads/{filename}', 'filename': filename})

@fastapi_app.post("/save_package")
async def save_package(request: Request):
    data = await request.json()
    package_name = data.get('package_name', 'default_package')
    
    package_dir = os.path.join(PACKAGES_DIR, package_name)
    os.makedirs(package_dir, exist_ok=True)
    
    json_path = os.path.join(package_dir, 'package.json')
    with open(json_path, 'w') as f:
        json.dump(data, f, indent=4)
        
    garment_filename = data.get('garment_filename')
    if garment_filename:
        src_img = os.path.join(UPLOADS_DIR, garment_filename)
        if os.path.exists(src_img):
            shutil.copy(src_img, os.path.join(package_dir, garment_filename))
            
    return JSONResponse({'success': True, 'path': package_dir})

def _studio_safe_name(value: str, *, field_name: str) -> str:
    cleaned = Path(value).name.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}.")
    return cleaned


def _studio_safe_subdir(value: str, *, field_name: str) -> str:
    cleaned = value.strip().strip("/\\")
    if not cleaned or cleaned in {".", ".."} or Path(cleaned).name != cleaned:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}.")
    return cleaned


def _studio_resolve_relative(base_dir: Path | str, relative_path: str, *, field_name: str) -> Path:
    root = Path(base_dir).resolve()
    candidate = (root / relative_path.lstrip("/\\")).resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}.")
    return candidate


def _replace_fastapi_route(path: str, methods: set[str], endpoint) -> None:
    if "fastapi_app" not in globals():
        return
    fastapi_app.router.routes = [
        route for route in fastapi_app.router.routes
        if not (
            getattr(route, "path", None) == path
            and methods.issubset(set(getattr(route, "methods", set())))
        )
    ]
    fastapi_app.add_api_route(path, endpoint, methods=list(methods))


class TryOnApiRequest(BaseModel):
    person_image_path: str
    garment_image_path: str
    output_image_path: str
    category: str = "Upper (T-Shirts, Hoodies)"
    sleeve_length: str = "default"
    pant_length: str = "default"
    resolution: str = "High Quality"
    steps: int = 24
    guidance: float = 3.5
    seed: int = 42
    show_mask: bool = False
    mask_sharpness: int = 12
    mask_padding: int = 6
    detail_boost: float = 0.0
    face_restore_strength: float = 0.0
    preserve_head: bool = False
    lock_seed: bool = True
    use_vae_hf: bool = True
    sampler_name: str = "Euler A"
    composite_strength: float = 0.0
    enable_deep_texture: bool = False
    warp_strength: float = 1.0


class StudioPackageRequest(BaseModel):
    package_name: str
    garment_filename: str
    mannequin_view: str
    pant_length: str = "default"
    sleeve_length: str = "default"
    keypoints: list[dict[str, object]] = Field(default_factory=list)


def _run_tryon_api_job(payload: TryOnApiRequest) -> dict[str, object]:
    from PIL import Image

    if not _READY.is_set():
        raise HTTPException(status_code=503, detail="Models are still loading.")
    if _ERROR:
        raise HTTPException(status_code=500, detail=f"Model load error: {_ERROR}")

    person_path = Path(payload.person_image_path).expanduser().resolve()
    garment_path = Path(payload.garment_image_path).expanduser().resolve()
    output_path = Path(payload.output_image_path).expanduser().resolve()

    if not person_path.exists():
        raise HTTPException(status_code=400, detail=f"Person image not found: {person_path}")
    if not garment_path.exists():
        raise HTTPException(status_code=400, detail=f"Garment image not found: {garment_path}")

    person_img = Image.open(person_path).convert("RGB")
    cloth_img = Image.open(garment_path).convert("RGB")

    result_img = None
    mask_img = None
    status_text = None
    for result_img, mask_img, status_text, _, _ in _inference(
        person_img,
        cloth_img,
        payload.category,
        payload.sleeve_length,
        payload.pant_length,
        payload.resolution,
        payload.steps,
        payload.guidance,
        payload.seed,
        payload.show_mask,
        payload.mask_sharpness,
        payload.mask_padding,
        payload.detail_boost,
        payload.face_restore_strength,
        payload.preserve_head,
        payload.lock_seed,
        payload.use_vae_hf,
        payload.sampler_name,
        None,
        payload.composite_strength,
        payload.enable_deep_texture,
        payload.warp_strength,
    ):
        pass

    if result_img is None:
        raise HTTPException(status_code=500, detail=f"Try-on did not produce an image. Status: {status_text}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result_img.save(output_path)

    response = {
        "status": "succeeded",
        "output_image_path": str(output_path),
        "message": status_text or "ok",
    }
    if payload.show_mask and mask_img is not None:
        mask_path = output_path.with_name(f"{output_path.stem}__mask{output_path.suffix}")
        mask_img.save(mask_path)
        response["mask_image_path"] = str(mask_path)
    return response


if "fastapi_app" in globals():
    async def _safe_upload_garment(file: UploadFile = File(...)):
        filename = _studio_safe_name(file.filename, field_name="filename")
        upload_dir = Path(UPLOADS_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)
        destination = upload_dir / filename
        with open(destination, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        return {"url": f"/uploads/{filename}", "filename": filename, "path": f"/uploads/{filename}"}


    async def _safe_save_package(request: Request):
        payload = StudioPackageRequest(**(await request.json()))
        safe_package = _studio_safe_subdir(payload.package_name, field_name="package name")
        garment_filename = _studio_safe_name(payload.garment_filename, field_name="garment filename")
        package_dir = Path(PACKAGES_DIR) / safe_package
        package_dir.mkdir(parents=True, exist_ok=True)

        source_image_path = _studio_resolve_relative(
            UPLOADS_DIR,
            garment_filename,
            field_name="garment filename",
        )
        if not source_image_path.exists():
            raise HTTPException(status_code=404, detail="Garment image not found.")

        destination_image_path = package_dir / "garment.png"
        shutil.copy(source_image_path, destination_image_path)

        metadata = {
            "name": safe_package,
            "category": None,
            "mannequin_view": payload.mannequin_view,
            "pant_length": payload.pant_length,
            "sleeve_length": payload.sleeve_length,
            "keypoints": payload.keypoints,
            "template_file": None,
        }
        with open(package_dir / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=4)

        with open(package_dir / "package.json", "w") as f:
            json.dump(payload.model_dump(), f, indent=4)

        return JSONResponse({"success": True, "path": str(package_dir)})


    _replace_fastapi_route("/upload_garment", {"POST"}, _safe_upload_garment)
    _replace_fastapi_route("/save_package", {"POST"}, _safe_save_package)

    @fastapi_app.post("/api/tryon/run")
    async def run_tryon_api(payload: TryOnApiRequest):
        return JSONResponse(_run_tryon_api_job(payload))


_original_inference = _inference


def _inference(
    person_img,
    cloth_img,
    category,
    sleeve_length,
    pant_length,
    resolution,
    num_steps,
    guidance,
    seed,
    show_mask,
    mask_sharpness,
    mask_padding,
    detail_boost,
    face_restore_strength,
    preserve_head,
    lock_seed,
    use_vae_hf,
    sampler_name,
    bg_plate,
    composite_strength,
    enable_deep_texture,
    warp_strength,
    progress=gr.Progress(),
):
    if resolution == "High Quality":
        num_steps = max(int(num_steps), 20)
        guidance = max(float(guidance), 3.0)
        if category == "Upper (T-Shirts, Hoodies)":
            mask_padding = max(int(mask_padding), 6)

    yield from _original_inference(
        person_img,
        cloth_img,
        category,
        sleeve_length,
        pant_length,
        resolution,
        num_steps,
        guidance,
        seed,
        show_mask,
        mask_sharpness,
        mask_padding,
        detail_boost,
        face_restore_strength,
        preserve_head,
        lock_seed,
        use_vae_hf,
        sampler_name,
        bg_plate,
        composite_strength,
        enable_deep_texture,
        warp_strength,
        progress=progress,
    )


if __name__ == "__main__":
    threading.Thread(target=_load_models, daemon=True).start()
    demo = build_ui()

    # Build unified dark theme using Gradio's theming API.
    # This is the ONLY correct way to control Gradio's compiled Svelte styles.
    # Do NOT use CSS variable injection for Gradio colours — it is ignored by Gradio's shadow DOM.
    gradio_theme = gr.themes.Base(
        font=gr.themes.GoogleFont("Inter"),
        font_mono=gr.themes.GoogleFont("JetBrains Mono"),
    ).set(
        body_background_fill="#0b0b0f",
        body_background_fill_dark="#0b0b0f",
        block_background_fill="#16161e",
        block_background_fill_dark="#16161e",
        block_border_color="#2a2a37",
        block_border_color_dark="#2a2a37",
        panel_background_fill="#16161e",
        panel_background_fill_dark="#16161e",
        panel_border_color="#2a2a37",
        panel_border_color_dark="#2a2a37",
        input_background_fill="#1f1f28",
        input_background_fill_dark="#1f1f28",
        input_border_color="#3a3a4a",
        input_border_color_dark="#3a3a4a",
        input_border_color_focus="#7e9cd8",
        input_border_color_focus_dark="#7e9cd8",
        body_text_color="#dcd7ba",
        body_text_color_dark="#dcd7ba",
        block_title_text_color="#dcd7ba",
        block_title_text_color_dark="#dcd7ba",
        block_label_text_color="#727169",
        block_label_text_color_dark="#727169",
        input_placeholder_color="#727169",
        input_placeholder_color_dark="#727169",
        button_primary_background_fill="#7e9cd8",
        button_primary_background_fill_dark="#7e9cd8",
        button_primary_background_fill_hover="#b4befe",
        button_primary_background_fill_hover_dark="#b4befe",
        button_primary_text_color="#0b0b0f",
        button_primary_text_color_dark="#0b0b0f",
        button_secondary_background_fill="#1f1f28",
        button_secondary_background_fill_dark="#1f1f28",
        button_secondary_background_fill_hover="#2a2a37",
        button_secondary_background_fill_hover_dark="#2a2a37",
        button_secondary_text_color="#dcd7ba",
        button_secondary_text_color_dark="#dcd7ba",
        slider_color="#7e9cd8",
        slider_color_dark="#7e9cd8",
        block_radius="12px",
        input_radius="4px",
        button_small_radius="4px",
        button_large_radius="4px",
        container_radius="12px",
    )

    # Minimal structural CSS only — no colours, those are owned by gradio_theme.
    gradio_extra_css = "footer, .built-with-gradio { display: none !important; }"

    app = gr.mount_gradio_app(fastapi_app, demo, path="/try-on", theme=gradio_theme, css=gradio_extra_css)

    uvicorn.run(app, host="127.0.0.1", port=7860)
