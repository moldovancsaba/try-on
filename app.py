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

# ── Apple Silicon & Environment Optimization ──────────────────────────────────
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
# Centralize HuggingFace Cache and enforce Absolute Offline Mode
os.environ["HF_HOME"] = "/Users/Shared/Models/.cache/huggingface"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# Silence Verbose Engine Logs
import logging
logging.getLogger("insightface").setLevel(logging.WARNING)
logging.getLogger("onnxruntime").setLevel(logging.ERROR)


import torch
import logging
import warnings

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
_MODELS_ROOT  = Path("/Users/Shared/Models")

# Internal sub-paths used by the package (Redirected to Master Standard Vault)
_D2_ROOT      = _CATVTON_ROOT / "model" / "SCHP" / "mhp_extension" / "detectron2"
_DP_ROOT      = _D2_ROOT / "projects" / "DensePose"
_MODELS_CAT   = _MODELS_ROOT / "processors" / "catvton-segmentation"
_MODELS_SD    = _MODELS_ROOT / "checkpoints" / "sd15-inpainting"
_MODELS_VAE   = _MODELS_ROOT / "vae" / "sd15-vae-ft-mse"
_LORA_LCM     = _MODELS_ROOT / "loras" / "sd15-lcm" / "pytorch_lora_weights.safetensors"
_MODELS_UP    = _MODELS_ROOT / "processors" / "upscalers"
_MODELS_GF    = _MODELS_ROOT / "processors" / "face-restoration"

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
_UPSCALER = None
_FACE_ENHANCER = None
_FACE_APP = None # InsightFace Analysis
_LOADED_VAE_TYPE = "hf" 
_FACEID_LOADED = False
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
                    
                    face_path = _MODELS_GF / "GFPGANv1.3.pth"
                    if face_path.exists():
                        _FACE_ENHANCER = GFPGANer(model_path=str(face_path), upscale=1, arch='clean', channel_multiplier=2, device=pipe_device)
            except Exception:
                pass
            
            # Initialize FaceID Mirror (InsightFace) with Absolute Silence
            try:
                import logging
                import contextlib
                import io
                
                # Squelch ALL engine output during handshake
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    from insightface.app import FaceAnalysis
                    _FACE_APP = FaceAnalysis(name='antelopev2', root=str(_MODELS_ROOT / "analysis" / "insightface"), providers=['CPUExecutionProvider'])
                    _FACE_APP.prepare(ctx_id=0, det_size=(640, 640))
                
                print("[try-on] Identity Mirror: ONLINE")
            except Exception as e:
                print(f"[warning] FaceID Mirror init fail: {e}")

        _READY.set()
        print(f"[try-on] \u2713 Ready | Backend: {pipe_device.upper()}")
        
    except Exception as exc:
        import traceback
        _ERROR = f"{exc}\n{traceback.format_exc()}"
        _READY.set()
        print(f"[try-on] Load failed: {exc}")

def _inference(person_img, cloth_img, category, sleeve_length, pant_length, resolution, num_steps, guidance, seed, show_mask, mask_sharpness, mask_padding, detail_boost, face_restore_strength, preserve_head, lock_seed, use_vae_hf, use_faceid, faceid_strength, sampler_name, bg_plate, composite_strength, enable_deep_texture, warp_strength, progress=gr.Progress()):
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
            "lock_seed": lock_seed, "use_vae_hf": use_vae_hf, "use_faceid": use_faceid, "faceid_strength": faceid_strength, 
            "sampler_name": sampler_name, "composite_strength": composite_strength,
            "enable_deep_texture": enable_deep_texture, "warp_strength": warp_strength
        }
        with open(_MODELS_ROOT / "settings.json", "w") as f:
            json.dump(settings, f)
    except Exception as e:
        print(f"[warning] Failed to save settings: {e}")

    # 🎭 Neural VAE Hot-Swap & Identity State
    global _LOADED_VAE_TYPE, _FACEID_LOADED
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

    # 🧬 Identity Anchor Handshake
    faceid_embeds = None
    if use_faceid and _FACE_APP:
        if not _FACEID_LOADED:
            progress(0, desc="Loading Identity Anchor weights...")
            bin_path = str(_MODELS_ROOT / "adapters" / "ip-adapter-faceid-sd15" / "ip-adapter-faceid_sd15.bin")
            lora_path = str(_MODELS_ROOT / "loras" / "sd15-faceid" / "ip-adapter-faceid_sd15_lora.safetensors")
            with _LOCK:
                _PIPE.load_faceid_adapter(bin_path, lora_path)
                _FACEID_LOADED = True
        
        # Scan face from person image
        import numpy as np
        person_np = np.array(person_img)
        # Convert RGB to BGR for InsightFace
        face_info = _FACE_APP.get(person_np[:, :, ::-1])
        if face_info:
            # Take the largest face if multiple are found
            face_info = sorted(face_info, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]), reverse=True)[0]
            faceid_embeds = torch.from_numpy(face_info.embedding).unsqueeze(0)
            print(f"[try-on] Identity Anchor Extracted.")
        else:
            print(f"[warning] No face found in person photo for FaceID.")

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

    # Resolution handling for speed
    target_size = (384, 512) if resolution == "Fast (Draft)" else (768, 1024)
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

    mask_pil = VaeImageProcessor(
        vae_scale_factor=8, do_normalize=False,
        do_binarize=True, do_convert_grayscale=True,
    ).blur(mask_pil, blur_factor=actual_blur)
    t_mask = time.monotonic() - t_start
    
    # 2. Diffusion
    progress(0.2, desc=f"Masking done ({t_mask:.1f}s). Starting diffusion...")
    
    # Stability: Clear cache and synchronize for Apple Silicon
    if torch.backends.mps.is_available():
        import torch.mps
        torch.mps.empty_cache()
        torch.mps.synchronize()
        
    gen = torch.Generator(device="mps").manual_seed(actual_seed)
    print(f"[try-on] Run: res={resolution}, steps={num_steps}, guidance={guidance}, seed={actual_seed}")
    
    # Speed Optimization & Likeness Toggles (Dynamic LoRA Handling)
    from diffusers import LCMScheduler, EulerAncestralDiscreteScheduler, DPMSolverMultistepScheduler, UniPCMultistepScheduler
    
    active_adapters = []
    adapter_weights = []
    
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if int(num_steps) < 10:
            _PIPE.noise_scheduler = LCMScheduler.from_config(_PIPE.noise_scheduler.config)
            # LCM Safety Clamp: guidance over 2.0 burns the image
            actual_guidance = min(float(guidance), 2.0)
            active_adapters.append("lcm")
            adapter_weights.append(1.0)
        else:
            if sampler_name == "DPM++ 2M":
                config = _PIPE.noise_scheduler.config
                _PIPE.noise_scheduler = DPMSolverMultistepScheduler.from_config(config, use_karras_sigmas=True)
            elif sampler_name == "UniPC":
                _PIPE.noise_scheduler = UniPCMultistepScheduler.from_config(_PIPE.noise_scheduler.config)
            else:
                _PIPE.noise_scheduler = EulerAncestralDiscreteScheduler.from_config(_PIPE.noise_scheduler.config)
            actual_guidance = float(guidance)
            
    if use_faceid and _FACEID_LOADED:
        active_adapters.append("faceid")
        adapter_weights.append(float(faceid_strength))
        
    try:
        if hasattr(_PIPE.unet, "set_adapters"):
            if len(active_adapters) > 0:
                _PIPE.unet.set_adapters(active_adapters, adapter_weights)
                _PIPE.unet.enable_adapters()
            else:
                _PIPE.unet.disable_adapters()
    except Exception as e:
        if "No adapter loaded" not in str(e):
            print(f"[warning] Adapter setup failed: {e}")

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
            faceid_embeds=faceid_embeds
        ):
            if latents is not None:
                # Decode intermediate state for live preview
                if isinstance(latents, torch.Tensor):
                    # Fast decode for preview
                    with torch.no_grad():
                        l = 1 / _PIPE.vae.config.scaling_factor * latents
                        # Take only the first latent in the concat (the person)
                        l = l.split(l.shape[-2] // 2, dim=-2)[0]
                        preview = _PIPE.vae.decode(l.to(_PIPE.device, dtype=_PIPE.vae_dtype)).sample
                        preview = (preview / 2 + 0.5).clamp(0, 1)
                        if _PIPE.device == "mps":
                            preview = preview.float()
                        preview = preview.cpu().permute(0, 2, 3, 1).numpy()
                        preview_img = numpy_to_pil(preview)[0]
                        yield preview_img, None, f"🎞️ Building... {int((i/int(num_steps))*100)}%", gr.update(), gr.update()
            else:
                # latents holds the final image when t is None (the third slot)
                result_img = latents 
                pass

        # Final yield from pipeline is (steps, None, image)
        if result_img is None:
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
                resolution = gr.Radio(["Fast (Draft)", "High Quality"], value=s.get("resolution", "Fast (Draft)"), label="Resolution")
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
                    use_faceid = gr.Checkbox(label="Face Identity Anchor (FaceID)", value=s.get("use_faceid", False))
                    faceid_strength = gr.Slider(0.0, 1.0, value=s.get("faceid_strength", 0.6), step=0.1, label="Likeness Strength (FaceID)")
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
        def apply_preset(res):
            if res == "Fast (Draft)":
                return (
                    gr.update(value=8),    # steps
                    gr.update(value=1.5),  # guidance (safe LCM zone)
                    gr.update(value=8),    # mask_sharpness
                    gr.update(value=3),    # mask_padding (less aggressive at low res)
                    gr.update(value=0.0),  # detail_boost (pointless at 384px)
                    gr.update(value=0.0),  # face_restore_strength (skip on draft)
                    gr.update(value=False), # preserve_head (resize kills quality)
                )
            else:  # High Quality
                return (
                    gr.update(value=30),   # steps
                    gr.update(value=3.5),  # guidance (full Euler sweet spot)
                    gr.update(value=12),   # mask_sharpness
                    gr.update(value=5),    # mask_padding
                    gr.update(value=0.4),  # detail_boost
                    gr.update(value=0.6),  # face_restore_strength (natural blend)
                    gr.update(value=True),  # preserve_head
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
                show_mask, mask_sharpness, mask_padding, detail_boost, face_restore_strength, preserve_head, lock_seed, use_vae_hf, use_faceid,
                faceid_strength, sampler, bg_plate, composite_strength, enable_deep_texture, warp_strength
            ],
            outputs=[result_out, mask_out, status_out, seed, run_btn],
            show_progress="hidden"
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
