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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import gradio as gr
from model_paths import (
    get_hf_home,
    get_models_root,
    load_settings as load_saved_settings,
    migrate_legacy_settings,
    save_settings as save_app_settings,
)
from pymongo import UpdateOne
from services.capabilities import (
    build_capability_report,
    feature_is_available,
    feature_status_message,
    render_capability_markdown,
)
from services.output_artifacts import build_output_metadata, write_sidecar_metadata
from services.quality_contracts import get_quality_contracts, validate_image_output
from services.mongo_uri import normalize_mongodb_uri
from services.service_manager import get_managed_services_status, perform_service_action
from services.single_task_lock import SingleTaskLock
from services.worker_contracts import PROCESSING_PROFILE_GENERIC, PROCESSING_PROFILE_MOTOGP, normalize_processing_profile
from services.tryon_setups import SETUP_PROVIDER_LOCAL, SETUP_PROVIDER_ONLINE, load_local_setups
from services.garment_packages import PACKAGE_SCHEMA_VERSION, load_garment_package
from services.local_ai_services import (
    evaluate_model_packs,
    export_report_csv,
    run_local_ai_service,
    service_registry,
)
from services.worker_runtime import append_worker_event, load_worker_status, read_recent_worker_events
from services.worker_settings import load_worker_settings, normalize_worker_settings, save_worker_settings

# ── Apple Silicon & Environment Optimization ──────────────────────────────────
_MODELS_ROOT = get_models_root()
_MODELS_ROOT_STR = str(_MODELS_ROOT)
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
# Centralize HuggingFace Cache and enforce Absolute Offline Mode
os.environ["HF_HOME"] = str(get_hf_home(_MODELS_ROOT))
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

# Shared-vault subpaths used by the local runtime and optional feature loaders.
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


def _runtime_state_snapshot() -> dict[str, Any]:
    gfpgan_ready: bool | None
    if _GFPGAN_READY:
        gfpgan_ready = True
    elif _GFPGAN_ERROR:
        gfpgan_ready = False
    else:
        gfpgan_ready = None

    return {
        "startup_error": _ERROR,
        "models_ready": bool(_READY.is_set() and not _ERROR),
        "gfpgan_ready": gfpgan_ready,
    }


def _refresh_capability_report() -> dict[str, Any]:
    global _CAPABILITY_REPORT
    _CAPABILITY_REPORT = build_capability_report(_MODELS_ROOT, runtime_state=_runtime_state_snapshot())
    return _CAPABILITY_REPORT


_WORKER_RESTART_BLOCK_STALE_SECONDS = 900


def _parse_iso_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _is_runtime_job_active(runtime_state: dict[str, Any]) -> bool:
    current_job_id = runtime_state.get("currentJobId")
    if not current_job_id:
        return False

    if not bool(runtime_state.get("workerRunning")):
        return False

    last_heartbeat = _parse_iso_utc(runtime_state.get("lastHeartbeatAt"))
    last_signal = last_heartbeat or _parse_iso_utc(runtime_state.get("lastLoopAt"))
    if last_signal is None:
        return True

    now = datetime.now(timezone.utc)
    signal_time = last_signal if last_signal.tzinfo else last_signal.replace(tzinfo=timezone.utc)
    return (now - signal_time).total_seconds() <= _WORKER_RESTART_BLOCK_STALE_SECONDS


def _get_capability_report() -> dict[str, Any]:
    return _CAPABILITY_REPORT or _refresh_capability_report()


def _load_local_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        os.environ.setdefault(key, value)


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
    This registers the vendored package and its submodules explicitly so
    relative imports work without requiring global PYTHONPATH changes.
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
_TRYON_TASK_LOCK = threading.Lock()
_PIPE    = None
_MASKER  = None
_ERROR   = None
_FACE_ENHANCER = None
_LOADED_VAE_TYPE = "hf" 
_GFPGAN_READY = False
_GFPGAN_ERROR = None
_READY   = threading.Event()
_CAPABILITY_REPORT: dict[str, Any] | None = None

_CATEGORY_UPPER = "Upper (T-Shirts, Hoodies)"
_CATEGORY_LOWER = "Lower (Jeans, Shorts, Skirts)"
_CATEGORY_FULL_BODY = "Full-Body (Suits, Dresses, Rompers)"
_CATEGORY_OUTER = "Outerwear (Jackets, Coats)"
_CATEGORY_CHOICES = [
    _CATEGORY_UPPER,
    _CATEGORY_LOWER,
    _CATEGORY_FULL_BODY,
    _CATEGORY_OUTER,
]
_CATEGORY_ALIASES = {
    "upper": _CATEGORY_UPPER,
    "lower": _CATEGORY_LOWER,
    "dresses": _CATEGORY_FULL_BODY,
    "overall": _CATEGORY_FULL_BODY,
    "Dresses (Full-Body, Suits, Rompers)": _CATEGORY_FULL_BODY,
    "Full-Body (Suits, Dresses, Rompers)": _CATEGORY_FULL_BODY,
    "outer": _CATEGORY_OUTER,
}
_CATEGORY_TO_AUTOMASK = {
    _CATEGORY_UPPER: "upper",
    _CATEGORY_LOWER: "lower",
    _CATEGORY_FULL_BODY: "overall",
    _CATEGORY_OUTER: "outer",
}


def _normalize_category(value: str | None) -> str:
    if not value:
        return _CATEGORY_UPPER
    return _CATEGORY_ALIASES.get(value, value if value in _CATEGORY_CHOICES else _CATEGORY_UPPER)


def _has_alpha_in_image(cloth_img: Any) -> bool:
    if not hasattr(cloth_img, "getbands") or not hasattr(cloth_img, "info") or not hasattr(cloth_img, "mode"):
        return False
    if "A" in cloth_img.getbands():
        return True
    return cloth_img.mode == "P" and cloth_img.info.get("transparency") is not None


def _garment_image_has_alpha(path_value: str | None) -> bool:
    if not path_value:
        return False
    image_path = Path(path_value).expanduser()
    if not image_path.exists():
        return False
    try:
        with Image.open(image_path) as image:
            return _has_alpha_in_image(image)
    except Exception:
        return False


def _normalize_garment_alpha_image(cloth_img):
    if _has_alpha_in_image(cloth_img):
        return cloth_img
    if getattr(cloth_img, "mode", "") == "P" and cloth_img.info.get("transparency") is not None:
        return cloth_img.convert("RGBA")
    return None


def _alpha_mask_from_garment(cloth_img, target_size: tuple[int, int]):
    rgba_img = _normalize_garment_alpha_image(cloth_img)
    if rgba_img is None:
        return None

    import numpy as np
    from PIL import Image

    alpha = rgba_img.getchannel("A")
    alpha_np = np.array(alpha, dtype=np.uint8)
    if alpha_np.size == 0 or alpha_np.max() <= 0 or np.all(alpha_np >= 255):
        return None

    w, h = alpha.size
    target_w, target_h = target_size
    if w / h < target_w / target_h:
        new_h = target_h
        new_w = w * target_h // h
    else:
        new_w = target_w
        new_h = h * target_w // w

    resized_alpha = alpha.resize((new_w, new_h), Image.LANCZOS)
    padded_alpha = Image.new("L", target_size, 0)
    padded_alpha.paste(resized_alpha, ((target_w - new_w) // 2, (target_h - new_h) // 2))
    return padded_alpha.point(lambda p: 255 if p >= 8 else 0, mode="L")

def _alpha_fill_color_from_garment_alpha(rgba: Any, alpha: Any) -> tuple[int, int, int]:
    import numpy as np

    rgb_np = np.array(rgba.convert("RGB"), dtype=np.float32)
    alpha_np = np.array(alpha, dtype=np.uint8)
    rgb_samples = rgb_np[alpha_np >= 254]
    if rgb_samples.size == 0:
        rgb_samples = rgb_np[alpha_np >= 48]
    if rgb_samples.size == 0:
        rgb_samples = rgb_np[alpha_np > 0]
    if rgb_samples.size == 0:
        return (0, 0, 0)

    mean_rgb = np.rint(rgb_samples.mean(axis=0)).astype(np.int16)
    return tuple(int(np.clip(value, 0, 255)) for value in mean_rgb.tolist())

def _flatten_garment_rgb_on_white(cloth_img):
    rgba = _normalize_garment_alpha_image(cloth_img)
    if rgba is None:
        return cloth_img.convert("RGB")

    from PIL import Image
    import numpy as np

    alpha = rgba.getchannel("A")
    alpha_np = np.array(alpha, dtype=np.uint8)
    if alpha_np.size == 0 or np.all(alpha_np >= 255):
        return rgba.convert("RGB")

    alpha_binary = alpha.point(lambda p: 255 if p >= 8 else 0, mode="L")
    fill_color = _alpha_fill_color_from_garment_alpha(rgba, alpha)
    neutral = Image.new("RGB", rgba.size, fill_color)
    neutral.paste(rgba.convert("RGB"), mask=alpha_binary)
    return neutral


def _build_identity_masks(mask_result: dict[str, Any], include_hair: bool = True) -> dict[str, Image.Image]:
    """
    Build the head-preservation mask from CatVTON's SCHP LIP parsing output.
    The face-swap path was removed, but the try-on flow still uses the head mask
    to composite the original head back over the generated garment result.
    """
    import numpy as np
    from PIL import Image

    schp_lip = np.array(mask_result["schp_lip"])
    # LIP labels used for a stable head matte:
    # 1 hat, 2 hair, 4 sunglasses, 13 face
    head_labels = {13, 4}
    if include_hair:
        head_labels.update({1, 2})

    head_mask_np = np.isin(schp_lip, list(head_labels)).astype(np.uint8) * 255
    head_mask = Image.fromarray(head_mask_np, mode="L")
    return {
        "head": head_mask,
        "selected": head_mask,
    }


# DensePose labels: 3 right hand, 4 left hand (vendor DENSE_INDEX_MAP).
_DENSEPOSE_HAND_LABELS = (3, 4)


def _build_hand_preserve_mask(mask_result: dict[str, Any]) -> Image.Image | None:
    """
    Build a hand-preservation mask from DensePose labels.

    Intended as a hard guard against diffusion repainting raised-arm hands, but
    currently unused: the render path forces preserve_hands=False, so nothing calls
    this with a truthy flag. Re-enable there before relying on it.
    """
    if "densepose" not in mask_result:
        return None

    import numpy as np
    from PIL import Image, ImageFilter

    densepose = np.array(mask_result["densepose"])
    if densepose.ndim != 2:
        return None

    hand_mask_np = np.isin(densepose, _DENSEPOSE_HAND_LABELS).astype(np.uint8) * 255
    if hand_mask_np.max() == 0:
        return None

    hand_mask = Image.fromarray(hand_mask_np, mode="L")
    hand_mask = hand_mask.filter(ImageFilter.MaxFilter(size=3))
    hand_mask = hand_mask.filter(ImageFilter.MinFilter(size=3))
    return hand_mask


def _build_full_body_edit_mask(
    mask_result: dict[str, Any],
    base_mask: Image.Image,
    head_mask: Image.Image | None = None,
) -> Image.Image:
    """
    Expand the editable region for full-body suits using SCHP parsing labels.
    AutoMasker is conservative on raised-arm poses, which leaves original
    jersey and shorts details visible. This unions the base mask with the
    parsed clothing and leg regions, then removes the preserved head matte.
    """
    import numpy as np
    from PIL import Image, ImageFilter

    schp_lip = np.array(mask_result["schp_lip"], dtype=np.uint8)
    base_arr = np.array(base_mask.convert("L"), dtype=np.uint8)

    # LIP parsing labels:
    # 5 upper-clothes, 6 dress, 7 coat, 8 socks, 9 pants,
    # 10 jumpsuits, 11 scarf, 12 skirt, 16/17 legs, 18/19 shoes
    full_body_labels = {5, 6, 7, 8, 9, 10, 11, 12, 16, 17, 18, 19}
    clothing_arr = (np.isin(schp_lip, list(full_body_labels)).astype(np.uint8) * 255)
    silhouette_arr = ((schp_lip > 0).astype(np.uint8) * 255)

    merged_arr = np.maximum(base_arr, clothing_arr).astype(np.uint8)
    merged_arr = np.where(silhouette_arr > 0, merged_arr, 0).astype(np.uint8)
    merged = Image.fromarray(merged_arr, mode="L")

    # Close small holes in the editable garment region without reopening the
    # arm gap around held objects, because the silhouette clamp already keeps
    # that gap outside the person parse.
    merged = merged.filter(ImageFilter.MaxFilter(size=7))
    merged = merged.filter(ImageFilter.MinFilter(size=7))

    if head_mask is not None:
        head_arr = np.array(head_mask.convert("L"), dtype=np.uint8)
        merged_arr = np.array(merged, dtype=np.uint8)
        merged_arr = np.where(head_arr > 0, 0, merged_arr).astype(np.uint8)
        merged = Image.fromarray(merged_arr, mode="L")

    return merged


def _composite_generated_garment(
    source_person: Image.Image,
    generated_result: Image.Image,
    garment_mask: Image.Image,
    feather_radius: float = 3.0,
) -> Image.Image:
    """
    Keep the original person image everywhere except the garment region.
    This preserves held objects, hands, and background details that the
    diffusion model should not be allowed to repaint.
    """
    from PIL import Image, ImageFilter

    src = source_person.resize(generated_result.size, Image.LANCZOS) if source_person.size != generated_result.size else source_person
    alpha = garment_mask.resize(generated_result.size, Image.LANCZOS) if garment_mask.size != generated_result.size else garment_mask
    alpha = alpha.convert("L")
    if feather_radius > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(radius=feather_radius))
    return Image.composite(generated_result, src, alpha)


def _prepare_diffusion_person(
    source_person: Image.Image,
    garment_mask: Image.Image,
    category: str,
) -> Image.Image:
    """
    Reduce source-garment branding bleed inside the editable region before
    diffusion. Full-body suits are the hardest case because strong chest logos
    and shorts details otherwise survive the inpaint too aggressively.
    """
    from PIL import Image, ImageFilter, ImageOps

    if category != _CATEGORY_FULL_BODY:
        return source_person

    alpha = garment_mask.convert("L").filter(ImageFilter.GaussianBlur(radius=6))
    blurred = source_person.filter(ImageFilter.GaussianBlur(radius=18))
    neutral = ImageOps.grayscale(blurred).convert("RGB")
    neutral = Image.blend(blurred, neutral, alpha=0.75)
    return Image.composite(neutral, source_person, alpha)


def _load_models():
    global _PIPE, _MASKER, _ERROR, _CAT_PKG, _READY, _FACE_ENHANCER
    global _GFPGAN_READY, _GFPGAN_ERROR
    import torch
    
    try:
        report = build_capability_report(_MODELS_ROOT)
        if not feature_is_available(report, "try_on"):
            raise RuntimeError(feature_status_message(report, "try_on"))

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
        # Force the CatVTON pipeline to use the shared-vault VAE path instead
        # of falling back to any bundled or remote default.
        os.environ["SMF_CATVTON_VAE_PATH"] = str(_MODELS_VAE)
        
        # weight_dtype below is the UNet/pipeline dtype, not the VAE dtype. The VAE is
        # deliberately kept at float32 on MPS to prevent colour drift on decode, but that
        # is enforced inside CatVTONPipeline (see `self.vae_dtype` in
        # vendor/CatVTON/model/pipeline.py), not here. Do not "reconcile" the
        # fp16-on-MPS argument below with that rule.
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

            # Load GFPGAN only when its local checkpoint and support weights are available.
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
        _refresh_capability_report()
        print("[try-on] Capability summary:")
        for feature_key in ("try_on",):
            print(f"[try-on] - {feature_status_message(_get_capability_report(), feature_key)}")
        print(f"[try-on] \u2713 Ready | Backend: {pipe_device.upper()}")
        
    except Exception as exc:
        import traceback
        _ERROR = f"{exc}\n{traceback.format_exc()}"
        _READY.set()
        _refresh_capability_report()
        print(f"[try-on] Load failed: {exc}")

def _inference(person_img, cloth_img, category, sleeve_length, pant_length, resolution, num_steps, guidance, seed, show_mask, mask_sharpness, mask_padding, detail_boost, face_restore_strength, preserve_head, lock_seed, use_vae_hf, sampler_name, bg_plate, composite_strength, enable_deep_texture, warp_strength, progress=gr.Progress(), *, mask_mode="default"):
    if not _TRYON_TASK_LOCK.acquire(blocking=False):
        yield None, None, "Try-On is already processing one job. Please wait for the current task to finish.", gr.update(), gr.update(interactive=True, value="Generate Try-On")
        return

    system_task_lock = SingleTaskLock("tryon-task", app_root=_ROOT)
    if not system_task_lock.acquire(blocking=False):
        _TRYON_TASK_LOCK.release()
        yield None, None, "Try-On is already processing one job. Please wait for the current task to finish.", gr.update(), gr.update(interactive=True, value="Generate Try-On")
        return

    try:
        yield from _run_inference_locked(person_img, cloth_img, category, sleeve_length, pant_length, resolution, num_steps, guidance, seed, show_mask, mask_sharpness, mask_padding, detail_boost, face_restore_strength, preserve_head, lock_seed, use_vae_hf, sampler_name, bg_plate, composite_strength, enable_deep_texture, warp_strength, progress, mask_mode=mask_mode)
    finally:
        system_task_lock.release()
        _TRYON_TASK_LOCK.release()


def _run_inference_locked(person_img, cloth_img, category, sleeve_length, pant_length, resolution, num_steps, guidance, seed, show_mask, mask_sharpness, mask_padding, detail_boost, face_restore_strength, preserve_head, lock_seed, use_vae_hf, sampler_name, bg_plate, composite_strength, enable_deep_texture, warp_strength, progress=gr.Progress(), *, mask_mode="default"):
    """Run one try-on render, yielding progress tuples until the final image.

    A generator, not a function: it yields
    `(image, mask, status_text, seed_update, button_update)` many times — live latent
    previews during diffusion, then the finished image — because Gradio streams from it
    and the button stays disabled until the last yield. Callers that want only the
    result (the API path) drain it and keep the last tuple.

    Order matters and each stage depends on the previous: parse the body (SCHP +
    DensePose) -> build and constrain the edit mask -> diffuse -> finish (face restore,
    sharpen, composite back). The masking stage is where most output quality is won or
    lost; diffusion just fills what the mask allows.

    Yields an error tuple and returns early rather than raising, for anything the user
    can act on: models still loading, missing assets, no person or garment image, a
    failed diffusion step, or output that fails the quality contract. Callers must
    check for a None image.

    Assumes the caller holds the try-on lock — it does not take one itself.
    """
    import torch
    import random
    import json
    from PIL import Image, ImageOps
    from diffusers.image_processor import VaeImageProcessor
    from diffusers import AutoencoderKL
    from catvton.utils import numpy_to_pil

    if not _READY.is_set():
        yield None, None, "⌛ Models loading... please wait.", gr.update(), gr.update()
        return
    if _ERROR:
        yield None, None, f"❌ Error: {_ERROR}", gr.update(), gr.update()
        return
    feature_key = "try_on"
    capability_report = _get_capability_report()
    if not feature_is_available(capability_report, "try_on"):
        yield None, None, feature_status_message(capability_report, "try_on"), gr.update(), gr.update(interactive=True, value="Generate Try-On")
        return

    if person_img is None:
        yield None, None, "Please upload a person/body image.", gr.update(), gr.update()
        return
    if cloth_img is None:
        yield None, None, "Please upload a garment image.", gr.update(), gr.update()
        return

    category = _normalize_category(category)

    # Preserve image fidelity for garments and hands.
    # - Disable experimental texture warp restoration (can blur logos/textures).
    # - Disable hand source-patching (prevents low-quality hand cutouts).
    # These are hard overrides on every entry path (UI and /api/tryon/run), so the
    # matching UI controls, the TryOnApiRequest fields, and the MotoGP profile's
    # warp_strength are all inert, and warp_repair.py is unreachable at runtime.
    # preserve_hands=False likewise makes _build_hand_preserve_mask and the hand
    # recomposite block dead code; hands stay protected upstream instead (see there).
    enable_deep_texture = False
    warp_strength = 0.0
    preserve_hands = False

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
        save_app_settings(settings, app_root=_ROOT)
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
    if cloth_img is not None and not isinstance(cloth_img, Image.Image):
        cloth_img = Image.fromarray(cloth_img)

    person_img = ImageOps.exif_transpose(person_img)
    if cloth_img is not None:
        cloth_img = ImageOps.exif_transpose(cloth_img)

    # Standalone build uses the stable high-quality render path only.
    target_size = (768, 1024)
    cloth_alpha_mask = None
    if cloth_img is not None:
        cloth_alpha_mask = _alpha_mask_from_garment(cloth_img, target_size)
        if cloth_alpha_mask is not None:
            mask_sharpness = max(int(mask_sharpness), 15)
            if mask_padding > 4:
                mask_padding = 4
        cloth_img = _flatten_garment_rgb_on_white(cloth_img)
    person = resize_and_crop(person_img.convert("RGB"), target_size)
    cloth = resize_and_padding(cloth_img.convert("RGB"), target_size) if cloth_img is not None else None
    
    # Masking logic: Invert sharpness to blur (15 sharpness = 0 blur)
    actual_blur = max(0.0, 15 - int(mask_sharpness))
    t_start = time.monotonic()
    progress(0, desc="Segmenting body...")
    
    # AutoMasker Mapping
    automask_category = _CATEGORY_TO_AUTOMASK.get(category, "upper")
    # try-on#38: expose_arms keeps the arm regions inside the edit mask so a
    # sleeveless garment renders with synthesized bare skin. The shrink
    # semantics of sleeve_length and the exposure mode are mutually exclusive
    # by construction inside cloth_agnostic_mask; forcing 'default' here makes
    # that visible at the call site too.
    if mask_mode == "expose_arms":
        sleeve_length = "default"
    mask_result = _MASKER(person, automask_category, sleeve_length=sleeve_length, pant_length=pant_length, expose_arms=(mask_mode == "expose_arms"))
    mask_pil = mask_result["mask"]
    hand_mask_pil = _build_hand_preserve_mask(mask_result) if preserve_hands else None

    # --- Identity Map Extraction ---
    import numpy as np
    schp_lip = np.array(mask_result["schp_lip"])
    person_silhouette_mask = Image.fromarray(((schp_lip > 0).astype(np.uint8) * 255), mode="L")
    
    # Build the optional head mask used for source-image head recomposition.
    head_mask_pil = None
    if preserve_head:
        identity_masks = _build_identity_masks(mask_result, include_hair=True)
        head_mask_pil = identity_masks["head"]

    if category == _CATEGORY_FULL_BODY:
        mask_pil = _build_full_body_edit_mask(mask_result, mask_pil, head_mask=head_mask_pil)
    
    # Advanced Mask Padding (Expand/Erode Silhouette)
    from PIL import ImageFilter
    if mask_padding > 0:
        mask_pil = mask_pil.filter(ImageFilter.MaxFilter(size=int(mask_padding * 2 + 1)))
    elif mask_padding < 0:
        mask_pil = mask_pil.filter(ImageFilter.MinFilter(size=int(abs(mask_padding) * 2 + 1)))

    # Constrain the garment mask to the parsed person silhouette so held
    # objects and background gaps between limbs do not get pulled into the
    # editable region by AutoMasker expansion.
    mask_arr = np.array(mask_pil.convert("L"), dtype=np.uint8)
    silhouette_arr = np.array(person_silhouette_mask, dtype=np.uint8)
    mask_arr = np.where(silhouette_arr > 0, mask_arr, 0).astype(np.uint8)
    mask_pil = Image.fromarray(mask_arr, mode="L")
    if cloth_alpha_mask is not None:
        constrained_mask = Image.composite(mask_pil, Image.new("L", mask_pil.size, 0), cloth_alpha_mask)
        if constrained_mask.getbbox():
            mask_pil = constrained_mask

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

    diffusion_person = _prepare_diffusion_person(person, mask_pil, category)
    t_mask = time.monotonic() - t_start
    
    result_img = None
    t_diff = 0.0
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

    try:
        # Decode intermediate latents for live preview only.
        for i, t, latents in _PIPE(
            image=diffusion_person,
            condition_image=cloth,
            mask=mask_pil,
            num_inference_steps=int(num_steps),
            guidance_scale=actual_guidance,
            generator=gen,
            callback_steps=4,
        ):
            if isinstance(latents, torch.Tensor):
                with torch.no_grad():
                    l = 1 / _PIPE.vae.config.scaling_factor * latents
                    l = l.split(l.shape[-2] // 2, dim=-2)[0]
                    preview = _PIPE.vae.decode(l.to(_PIPE.device, dtype=_PIPE.vae_dtype)).sample
                    preview = (preview / 2 + 0.5).clamp(0, 1)
                    if _PIPE.device == "mps":
                        preview = preview.float()
                    preview = preview.cpu().permute(0, 2, 3, 1).numpy()
                    preview_img = numpy_to_pil(preview)[0]
                    yield preview_img, None, f"🎞️ Building... {int((i / int(num_steps)) * 100)}%", gr.update(), gr.update()
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
                if cloth_alpha_mask is None:
                    garment_mask = garment_mask.filter(ImageFilter.GaussianBlur(radius=2))
                
                raw_img = Image.fromarray(img_np)
                result_img = Image.composite(sharpened_pil, raw_img, garment_mask)
            else:
                result_img = sharpened_pil
                
            img_np = np.array(result_img)
    
    result_img = Image.fromarray(img_np)
    
    # Restore higher-frequency garment texture details from the source image.
    # Unreachable: enable_deep_texture is forced False by the fidelity overrides above.
    # Kept so the pass can be restored by dropping that override; delete this branch and
    # warp_repair.py together if the feature is abandoned for good.
    if enable_deep_texture and cloth_img is not None:
        progress(0.91, desc="Warping Original Textures...")
        from warp_repair import texture_repair_pass
        result_img = texture_repair_pass(cloth_img, result_img, mask_pil, warp_strength=warp_strength)

    # Preserve original content outside the garment mask so held objects,
    # hands, and background details do not get repainted by diffusion.
    if mask_pil is not None:
        progress(0.915, desc="Preserving non-garment content...")
        composite_radius = 0.0 if cloth_alpha_mask is not None else 2.0
        result_img = _composite_generated_garment(person, result_img, mask_pil, feather_radius=composite_radius)

    # Unreachable: preserve_hands is forced False above, so hand_mask_pil is always None.
    # Hands are not unprotected, though — AutoMasker keeps hands/feet out of the edit
    # mask (hands_protect_area in the vendored cloth_masker), and the composite step
    # above restores everything outside the garment mask from the source photo.
    if hand_mask_pil is not None:
        progress(0.918, desc="Preserving hands...")
        hand_src = person.resize(result_img.size, Image.LANCZOS) if person.size != result_img.size else person
        hand_alpha = hand_mask_pil.resize(result_img.size, Image.LANCZOS) if hand_mask_pil.size != result_img.size else hand_mask_pil
        hand_alpha = hand_alpha.filter(ImageFilter.GaussianBlur(radius=1.0))
        result_img = Image.composite(hand_src, result_img, hand_alpha)
    
    # Re-composite the preserved head region from the source person image.
    if preserve_head and head_mask_pil is not None:
        progress(0.92, desc="Recompositing preserved head region...")
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

    validation = validate_image_output(feature_key, result_img, mask=mask_pil if show_mask else None)
    if not validation["passed"]:
        detail = "; ".join(validation["failures"])
        yield None, None, f"❌ Output failed quality validation: {detail}", gr.update(), gr.update(interactive=True, value="Generate Try-On")
        return

    mask_out = mask_pil if show_mask else None
    warning_suffix = ""
    if validation["warnings"]:
        warning_suffix = " | Warnings: " + "; ".join(validation["warnings"])
    yield result_img, mask_out, f"✓ Try-On Ready | Latency: {t_mask+t_diff:.1f}s{warning_suffix}", gr.update(), gr.update(interactive=True, value="Generate Try-On")

# ── Gradio UI ─────────────────────────────────────────────────────────────────
def load_settings():
    migrate_legacy_settings(app_root=_ROOT, models_root=_MODELS_ROOT)
    return load_saved_settings(app_root=_ROOT, models_root=_MODELS_ROOT)

def build_ui(mode: str = "generic"):
    """Build the Gradio Blocks surface for one page and return it unlaunched.

    Called twice at startup — `mode="generic"` mounts at /try-on with the controls
    exposed, `mode="motogp"` mounts at /motogp-leather-magic with them locked to the
    tuned leather-suit values. Same render path underneath; the mode only decides which
    knobs the operator can move, and the MotoGP wrapper overrides its inputs before
    calling _inference regardless of what the components hold.

    Component visibility also depends on capability state at build time (face restore
    hides when GFPGAN is unavailable), so the UI is built after models load, and a
    vault change needs an app restart to show up.
    """
    s = load_settings()
    motogp_mode = mode == "motogp"
    nav_active = "motogp" if motogp_mode else "try-on"
    page_title = "MotoGP Leather Magic" if motogp_mode else "Lightweight Local Virtual Try-On"
    page_subtitle = (
        "Standardized A-pose workflow for full-body MotoGP leather suits."
        if motogp_mode
        else "Upload a person photo and garment to run the local try-on pipeline."
    )

    def run_core_tryon(
        person_img,
        cloth_img,
        category,
        sleeve_length,
        pant_length,
        resolution,
        steps,
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
        if motogp_mode:
            category = _CATEGORY_FULL_BODY
            sleeve_length = "default"
            pant_length = "default"
            resolution = "High Quality"
            steps = max(int(steps), 50)
            guidance = max(float(guidance), 4.6)
            mask_sharpness = max(int(mask_sharpness), 12)
            mask_padding = max(int(mask_padding), 10)
            detail_boost = max(0.0, min(float(detail_boost), 0.25))
            face_restore_strength = 0.0
            preserve_head = True
            lock_seed = True
            use_vae_hf = True
            sampler_name = "DPM++ 2M"
            bg_plate = None
            composite_strength = 0.0
            enable_deep_texture = False
            warp_strength = 1.0

        yield from _inference(
            person_img,
            cloth_img,
            category,
            sleeve_length,
            pant_length,
            resolution,
            steps,
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

    with gr.Blocks(title=page_title) as demo:
        gr.Markdown(render_capability_markdown(_get_capability_report(), feature_keys=("try_on",)))
        gr.HTML(get_navbar(nav_active))
        gr.HTML(_ops_banner_html)
        gr.Markdown(f"# {page_title}")
        gr.Markdown(page_subtitle)

        if motogp_mode:
            gr.Markdown(
                "\n".join(
                    [
                        "Use this mode when both inputs follow the standard MotoGP contract:",
                        "- person photo is full-body, straight camera, neutral background, A-pose",
                        "- garment image is a front-facing full-body leather suit",
                        "- the workflow is locked to full-body suit presets",
                    ]
                )
            )

        with gr.Row():
            with gr.Column():
                person_in = gr.Image(
                    label="A-Pose Person Photo" if motogp_mode else "Person Photo",
                    type="numpy",
                )
                cloth_in = gr.Image(
                    label="MotoGP Leather Suit" if motogp_mode else "Garment Image",
                    type="numpy",
                )
                saved_cat = _normalize_category(
                    s.get("category", _CATEGORY_FULL_BODY if motogp_mode else _CATEGORY_UPPER)
                )
                category_choices = [_CATEGORY_FULL_BODY] if motogp_mode else _CATEGORY_CHOICES
                category = gr.Dropdown(
                    category_choices,
                    value=_CATEGORY_FULL_BODY if motogp_mode else saved_cat,
                    label="Garment Category",
                    info=(
                        "Locked to full-body leather suits in MotoGP mode."
                        if motogp_mode
                        else "Use Full-Body for leather suits, dresses, and rompers."
                    ),
                    interactive=not motogp_mode,
                )
                if not motogp_mode:
                    with gr.Accordion("Garment Cut Constraints (Optional)", open=False):
                        sleeve_length = gr.Radio(
                            ["default", "short_sleeve", "sleeveless"],
                            value=s.get("sleeve_length", "default"),
                            label="Sleeve Length Limit",
                        )
                        pant_length = gr.Radio(
                            ["default", "shorts"],
                            value=s.get("pant_length", "default"),
                            label="Pant Length Limit",
                        )
                else:
                    sleeve_length = gr.State("default")
                    pant_length = gr.State("default")
                resolution = gr.Radio(["High Quality"], value="High Quality", label="Resolution")
                if not motogp_mode:
                    bg_plate = gr.Image(label="Background Plate (Optional)", type="numpy")
                else:
                    bg_plate = gr.State(None)

            with gr.Column():
                with gr.Group():
                    steps = gr.Slider(
                        4,
                        50,
                        value=50 if motogp_mode else s.get("steps", 20),
                        step=1,
                        label="Steps",
                        info="MotoGP mode uses 50 steps for better logo fidelity." if motogp_mode else None,
                    )
                    guidance = gr.Slider(
                        1.0,
                        5.0,
                        value=4.6 if motogp_mode else s.get("guidance", 3.5),
                        step=0.1,
                        label="Guidance",
                    )
                    mask_sharpness = gr.Slider(
                        0,
                        15,
                        value=12 if motogp_mode else s.get("mask_sharpness", 12),
                        step=1,
                        label="Logo & Pattern Sharpness",
                    )
                    mask_padding = gr.Slider(
                        -10,
                        30,
                        value=10 if motogp_mode else s.get("mask_padding", 5),
                        step=1,
                        label="Mask Padding",
                    )
                    detail_boost = gr.Slider(
                        0.0,
                        1.0,
                        value=0.25 if motogp_mode else s.get("detail_boost", 0.4),
                        step=0.1,
                        label="Logo/Pattern Detail Boost",
                    )
                    if not motogp_mode:
                        composite_strength = gr.Slider(
                            0.0,
                            1.0,
                            value=s.get("composite_strength", 0.0),
                            step=0.1,
                            label="Clean Plate Blend (0 = OFF)",
                        )
                    else:
                        composite_strength = gr.State(0.0)

                with gr.Row():
                    seed = gr.Number(value=s.get("seed", 42), label="Seed", precision=0, scale=4, container=False)
                    btn_42 = gr.Button("42", size="sm", min_width=60, scale=0)
                    btn_1337 = gr.Button("1337", size="sm", min_width=60, scale=0)
                    lock_seed = gr.Checkbox(
                        label="🔒 Lock",
                        value=True if motogp_mode else s.get("lock_seed", False),
                        scale=0,
                        container=False,
                        interactive=not motogp_mode,
                    )

                with gr.Accordion("Options" if not motogp_mode else "MotoGP Advanced", open=not motogp_mode):
                    preserve_head = gr.Checkbox(
                        label="Preserve Original Head ♥️ (Literal Pixel Paste)",
                        value=True if motogp_mode else s.get("preserve_head", True),
                        interactive=not motogp_mode,
                    )
                    use_vae_hf = gr.Checkbox(
                        label="High-Fidelity VAE (ft-mse)",
                        value=True if motogp_mode else s.get("use_vae_hf", True),
                        interactive=not motogp_mode,
                    )
                    if not motogp_mode:
                        face_restore_strength = gr.Slider(
                            0.0,
                            1.0,
                            value=s.get("face_restore_strength", 1.0),
                            step=0.1,
                            label="Face Restore Blend (GFPGAN)",
                        )
                    else:
                        face_restore_strength = gr.State(0.0)
                    sampler = gr.Dropdown(
                        ["Euler A", "DPM++ 2M", "UniPC"],
                        value="DPM++ 2M" if motogp_mode else s.get("sampler_name", "Euler A"),
                        label="High Quality Sampler",
                        interactive=not motogp_mode,
                    )
                    enable_deep_texture = gr.Checkbox(
                        label="Deep Logo & Texture Restoration (TPS Warp)",
                        value=False,
                        interactive=False,
                    )
                    warp_strength = gr.Slider(
                        0.0,
                        1.0,
                        value=0.0,
                        step=0.1,
                        label="Texture Warp Blend Force",
                        interactive=False,
                    )
                    show_mask = gr.Checkbox(
                        label="Show Masking Step (Debug)",
                        value=s.get("show_mask", False),
                    )

                run_btn = gr.Button(
                    "Generate MotoGP Leather Magic" if motogp_mode else "Generate Try-On",
                    variant="primary",
                )
                status_out = gr.Textbox(label="Status", interactive=False, container=False)
                result_out = gr.Image(label="Result", interactive=False)
                mask_out = gr.Image(label="Mask", visible=False)

        btn_42.click(fn=lambda: (42, True), outputs=[seed, lock_seed])
        btn_1337.click(fn=lambda: (1337, True), outputs=[seed, lock_seed])

        def apply_preset(_res):
            if motogp_mode:
                return (
                    gr.update(value=50),
                    gr.update(value=4.6),
                    gr.update(value=12),
                    gr.update(value=10),
                )
            return (
                gr.update(value=30),
                gr.update(value=3.5),
                gr.update(value=12),
                gr.update(value=5),
                gr.update(value=0.0),
                gr.update(value=0.0),
                gr.update(value=False),
            )

        if motogp_mode:
            resolution.change(
                fn=apply_preset,
                inputs=[resolution],
                outputs=[steps, guidance, mask_sharpness, mask_padding],
            )
        else:
            resolution.change(
                fn=apply_preset,
                inputs=[resolution],
                outputs=[steps, guidance, mask_sharpness, mask_padding, detail_boost, face_restore_strength, preserve_head],
            )

        show_mask.change(lambda v: gr.update(visible=v), show_mask, mask_out)
        run_btn.click(
            fn=run_core_tryon,
            inputs=[
                person_in, cloth_in, category, sleeve_length, pant_length, resolution, steps, guidance, seed,
                show_mask, mask_sharpness, mask_padding, detail_boost, face_restore_strength, preserve_head,
                lock_seed, use_vae_hf, sampler, bg_plate, composite_strength, enable_deep_texture, warp_strength
            ],
            outputs=[result_out, mask_out, status_out, seed, run_btn],
            show_progress="hidden",
        )

    return demo


from fastapi import FastAPI, HTTPException, Request, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import shutil
import json
from pydantic import BaseModel, Field

fastapi_app = FastAPI(title="try-on", version="12.2.0")

# SECURITY (try-on#42): the server binds 127.0.0.1 but browsers can reach
# loopback, so a web page the operator visits could POST to this API. Reject
# any request carrying a cross-origin Origin header. Requests with no Origin
# (curl, the queue worker's own calls) are unaffected; same-origin operator UI
# calls (Origin http://127.0.0.1:7860 / localhost) are allowed.
_ALLOWED_ORIGINS = {
    "http://127.0.0.1:7860", "http://localhost:7860",
}

@fastapi_app.middleware("http")
async def _origin_guard(request, call_next):
    origin = request.headers.get("origin")
    if origin and origin not in _ALLOWED_ORIGINS:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "forbidden origin"}, status_code=403)
    return await call_next(request)

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
fastapi_app.mount("/packages", StaticFiles(directory=PACKAGES_DIR), name="packages")
fastapi_app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def get_navbar(active="try-on"):
    with open(os.path.join(TEMPLATES_DIR, "navbar.html"), "r") as f:
        html = f.read()
    # Simple manual replacement for Gradio since we aren't using Jinja here
    for key in ("try-on", "motogp", "worker-control", "set-garment", "garments"):
        html = html.replace(
            "{{ 'active' if active == '%s' else '' }}" % key,
            "active" if active == key else "",
        )
        html = html.replace(
            "{{ 'page' if active == '%s' else 'false' }}" % key,
            "page" if active == key else "false",
        )
    return html


def _ops_banner_html() -> str:
    """Server-rendered snapshot banner (model readiness + worker state) for Gradio pages.

    ponytail: snapshot at page load, refreshes on reload — no client polling.
    """
    if _ERROR:
        models = ("error", "Models failed to load")
    elif _READY.is_set():
        models = ("ok", "Models ready")
    else:
        models = ("warn", "Models loading…")

    try:
        worker = load_worker_status(app_root=_ROOT)
        if worker.get("enabled") is False:
            wk = ("warn", "Worker disabled")
        elif worker.get("workerRunning"):
            wk = ("info", "Worker active") if _is_runtime_job_active(worker) else ("ok", "Worker idle")
        else:
            wk = ("error", "Worker stopped")
    except Exception:
        wk = ("neutral", "Worker state unknown")

    def _b(pair):
        variant, label = pair
        return f'<span class="badge badge--{variant}">{label}</span>'

    return (
        '<div class="ops-banner" role="status">'
        '<span class="ops-banner-label">Operations</span>'
        f"{_b(models)}{_b(wk)}"
        "</div>"
    )

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


@fastapi_app.get("/worker-control", response_class=HTMLResponse)
async def worker_control_page(request: Request):
    return templates.TemplateResponse(request=request, name="worker_control.html", context={"active": "worker-control"})

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
    garment_image_path: str | None = None
    garment_package_name: str | None = None
    output_image_path: str
    processing_profile: str = PROCESSING_PROFILE_GENERIC
    category: str = _CATEGORY_UPPER
    # WHAT: where the caller's category came from (try-on#37). 'garment_type'
    #     means the queue worker resolved it from the garment's own catalog
    #     type - a processing profile must then not override category or
    #     sleeve/pant length (it keeps its quality knobs). 'setup' preserves
    #     the historical behavior where the MotoGP profile forces Full-Body.
    category_source: str = "setup"
    # WHAT: masking mode (try-on#38). 'expose_arms' keeps the arm regions
    #     inside the edit mask so a sleeveless garment renders with
    #     synthesized bare skin instead of the source photo's sleeves. Only
    #     valid for Upper-category renders; validated, not coerced - a wrong
    #     mask mode produces an expensively wrong render, so fail fast.
    mask_mode: str = "default"
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
    # Accepted for wire compatibility but ignored: the render path forces both off.
    enable_deep_texture: bool = False
    warp_strength: float = 1.0


class StudioPackageRequest(BaseModel):
    package_name: str
    garment_filename: str
    mannequin_view: str
    pant_length: str = "default"
    sleeve_length: str = "default"
    keypoints: list[dict[str, object]] = Field(default_factory=list)


class WorkerSettingsRequest(BaseModel):
    enabled: bool
    pollIntervalSeconds: int
    updatedBy: str | None = None


class ServiceActionRequest(BaseModel):
    target: str
    action: str
    requestedBy: str | None = None


class RetryWorkerJobRequest(BaseModel):
    target: str = "queued"
    delayMinutes: int = 0
    requestedBy: str | None = None
    resetAttempts: bool = False


class TryOnSetupSelectionRequest(BaseModel):
    cameraId: str


class LocalAiJobRequest(BaseModel):
    serviceId: str
    payload: dict[str, Any] = Field(default_factory=dict)


def _apply_processing_profile(payload: TryOnApiRequest) -> TryOnApiRequest:
    profile = normalize_processing_profile(payload.processing_profile)
    payload.processing_profile = profile
    if profile == PROCESSING_PROFILE_MOTOGP:
        # try-on#37: when the worker resolved category from the garment's own
        # type, the profile keeps its quality knobs but must not stomp the
        # garment identity - a jersey routed through the MotoGP-tuned profile
        # would otherwise be forced back to a full-body mask.
        if payload.category_source != "garment_type":
            payload.category = _CATEGORY_FULL_BODY
            payload.sleeve_length = "default"
            payload.pant_length = "default"
        payload.resolution = "High Quality"
        has_alpha_garment = _garment_image_has_alpha(payload.garment_image_path)
        # High-fidelity Leather route tuned for logo and print clarity.
        payload.steps = max(int(payload.steps), 50)
        payload.guidance = max(float(payload.guidance), 4.6)
        payload.mask_sharpness = max(int(payload.mask_sharpness), 12)
        payload.mask_padding = max(int(payload.mask_padding), 10)
        if has_alpha_garment:
            # Transparent PNG garments should use tighter constraints to avoid halo fill.
            payload.mask_padding = min(payload.mask_padding, 4)
            payload.mask_sharpness = max(payload.mask_sharpness, 16)
        payload.detail_boost = max(0.0, min(float(payload.detail_boost), 0.25))
        payload.face_restore_strength = 0.0
        payload.preserve_head = True
        payload.lock_seed = True
        payload.use_vae_hf = True
        payload.sampler_name = "DPM++ 2M"
        payload.composite_strength = 0.0
        payload.enable_deep_texture = False
        payload.warp_strength = 1.0  # inert: zeroed again by the render path's fidelity overrides
    return payload


def _normalize_opt_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _resolve_tryon_db() -> tuple[str, str] | None:
    _load_local_env_file(_ROOT / ".env.tryon-worker")
    _load_local_env_file(_ROOT / ".env.local")
    mongodb_uri = (os.getenv("MONGODB_ATLAS_URI") or os.getenv("MONGODB_URI") or "").strip()
    mongodb_db_name = (os.getenv("MONGODB_DB_NAME") or os.getenv("MONGODB_DB") or "").strip()
    if not mongodb_uri or not mongodb_db_name:
        return None
    return mongodb_uri, mongodb_db_name


def _get_tryon_db():
    cfg = _resolve_tryon_db()
    if not cfg:
        return None, None
    from pymongo import MongoClient

    mongodb_uri, mongodb_db_name = cfg
    client = MongoClient(normalize_mongodb_uri(mongodb_uri), serverSelectionTimeoutMS=3000)
    return client, client[mongodb_db_name]


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _plus_minutes_iso(minutes: int) -> str:
    if minutes <= 0:
        return _now_utc_iso()
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _normalize_retry_target(target: str) -> str:
    normalized = (target or "").strip().lower()
    if normalized in {"queued", "queue"}:
        return "queued"
    if normalized in {"retry_wait", "retrywait", "retry-wait", "retry"}:
        return "retry_wait"
    raise ValueError("target must be one of 'queued' or 'retry_wait'")


def _sync_tryon_setups_from_local_catalog(db, setup_collection_name: str, local_setups: dict[str, dict[str, Any]]) -> None:
    if not local_setups:
        return
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    operations: list[UpdateOne] = []
    for setup in local_setups.values():
        setup_id = str(setup.get("setupId") or "").strip()
        if not setup_id or not bool(setup.get("active", True)):
            continue
        operations.append(
            UpdateOne(
                {"setupId": setup_id},
                {
                    "$set": {
                        "name": str(setup.get("name") or setup_id),
                        "description": setup.get("description"),
                        "cameraId": setup.get("cameraId"),
                        "active": True,
                        "isDefault": bool(setup.get("isDefault")),
                        "rank": int(setup.get("rank") or 0),
                        "revision": str(setup.get("revision") or ""),
                        "provider": str(setup.get("provider") or SETUP_PROVIDER_LOCAL),
                        "updatedAt": now,
                    },
                    "$setOnInsert": {
                        "setupId": setup_id,
                        "createdAt": now,
                    },
                },
                upsert=True,
            )
        )
    if operations:
        db[setup_collection_name].bulk_write(operations, ordered=False)


def _tryon_collection_names() -> tuple[str, str]:
    return (
        (os.getenv("TRYON_SETUP_COLLECTION") or "tryon_setups").strip(),
        (os.getenv("TRYON_CAMERA_SETUP_PREFERENCE_COLLECTION") or "camera_setup_preferences").strip(),
    )


def _build_worker_status_report() -> dict[str, object]:
    _load_local_env_file(_ROOT / ".env.tryon-worker")
    _load_local_env_file(_ROOT / ".env.local")
    runtime_state = load_worker_status(app_root=_ROOT)
    runtime_state["workerJobActive"] = _is_runtime_job_active(runtime_state)
    report = runtime_state
    report["settings"] = load_worker_settings(app_root=_ROOT)
    report["recentEvents"] = read_recent_worker_events(limit=20, app_root=_ROOT)
    report["services"] = get_managed_services_status(app_root=_ROOT, current_process_is_app=True)
    report["queueRoot"] = os.getenv("TRYON_QUEUE_ROOT", str(_ROOT / "queue"))
    report["localApiUrl"] = os.getenv("TRYON_LOCAL_API_URL", "http://127.0.0.1:7860/api/tryon/run")

    mongodb_uri = (os.getenv("MONGODB_ATLAS_URI") or os.getenv("MONGODB_URI") or "").strip()
    mongodb_db_name = (os.getenv("MONGODB_DB_NAME") or os.getenv("MONGODB_DB") or "").strip()
    queue_counts: dict[str, int] = {}
    if mongodb_uri and mongodb_db_name:
        try:
            from pymongo import MongoClient

            client = MongoClient(normalize_mongodb_uri(mongodb_uri), serverSelectionTimeoutMS=3000)
            db = client[mongodb_db_name]
            setup_collection_name, _ = _tryon_collection_names()
            for status in ("queued", "claimed", "processing", "uploading_result", "notifying_camera", "retry_wait", "done", "failed"):
                queue_counts[status] = int(db["tryon_jobs"].count_documents({"status": status}))
            report["activeSetups"] = int(db[setup_collection_name].count_documents({"active": True}))
            client.close()
        except Exception as error:
            report["queueError"] = str(error)
    report["queueCounts"] = queue_counts
    return report


def _run_tryon_api_job(payload: TryOnApiRequest) -> dict[str, object]:
    """Run a try-on from an API request and return the response body.

    Filesystem in, filesystem out: the caller supplies readable input paths and a
    writable output path, and no image bytes cross the API boundary. That is what lets
    the queue worker hand off a multi-hundred-megabyte job over localhost cheaply.

    Applies the processing profile first (which can override the caller's parameters —
    the MotoGP profile in particular), then drains the render generator and keeps the
    final frame. Raises HTTPException: 503 while models load, 500 on a load error or a
    render that produced nothing, 400 for missing inputs.
    """
    from PIL import Image, ImageOps

    payload = _apply_processing_profile(payload)

    # try-on#38: mask_mode is validated, never coerced - a wrong mask mode
    # produces an expensively wrong render, so fail fast with a named error.
    # expose_arms is only defined for upper-body garments; the category here
    # is post-profile and post-normalization, i.e. what will actually render.
    if payload.mask_mode not in ("default", "expose_arms"):
        raise HTTPException(status_code=400, detail=f"Unknown mask_mode: {payload.mask_mode!r} (allowed: default, expose_arms)")
    if payload.mask_mode == "expose_arms" and _normalize_category(payload.category) != _CATEGORY_UPPER:
        raise HTTPException(status_code=400, detail="mask_mode=expose_arms is only valid for Upper-category garments.")

    if not _READY.is_set():
        raise HTTPException(status_code=503, detail="Models are still loading.")
    if _ERROR:
        raise HTTPException(status_code=500, detail=f"Model load error: {_ERROR}")

    person_path = Path(payload.person_image_path).expanduser().resolve()
    if payload.garment_package_name:
        garment_package = load_garment_package(Path(PACKAGES_DIR), payload.garment_package_name)
        garment_path = garment_package.garment_path
    elif payload.garment_image_path:
        garment_path = Path(payload.garment_image_path).expanduser().resolve()
    else:
        raise HTTPException(status_code=400, detail="garment_image_path or garment_package_name is required.")
    output_path = Path(payload.output_image_path).expanduser().resolve()
    # SECURITY (try-on#42): constrain the output path to the project root so an
    # unauthenticated local caller cannot write a PNG to an arbitrary location.
    _project_root = Path(__file__).resolve().parent
    if not str(output_path).startswith(str(_project_root) + "/"):
        raise HTTPException(status_code=400, detail="output_image_path must be within the try-on workspace.")

    if not person_path.exists():
        raise HTTPException(status_code=400, detail=f"Person image not found: {person_path}")
    if not garment_path.exists():
        raise HTTPException(status_code=400, detail=f"Garment image not found: {garment_path}")

    person_img = ImageOps.exif_transpose(Image.open(person_path))
    cloth_img = ImageOps.exif_transpose(Image.open(garment_path))

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
        mask_mode=payload.mask_mode,
    ):
        pass

    if result_img is None:
        raise HTTPException(status_code=500, detail=f"Try-On did not produce an image. Status: {status_text}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result_img.save(output_path)

    response = {
        "status": "succeeded",
        "output_image_path": str(output_path),
        "message": status_text or "ok",
        "processing_profile": payload.processing_profile,
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
            "schemaVersion": PACKAGE_SCHEMA_VERSION,
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
            package_payload = payload.model_dump()
            package_payload["schemaVersion"] = PACKAGE_SCHEMA_VERSION
            package_payload["garment_file"] = "garment.png"
            json.dump(package_payload, f, indent=4)

        return JSONResponse({"success": True, "path": str(package_dir)})


    _replace_fastapi_route("/upload_garment", {"POST"}, _safe_upload_garment)
    _replace_fastapi_route("/save_package", {"POST"}, _safe_save_package)

    @fastapi_app.post("/api/tryon/run")
    async def run_tryon_api(payload: TryOnApiRequest):
        from PIL import Image

        response = _run_tryon_api_job(payload)
        output_path = Path(response["output_image_path"])
        result_img = Image.open(output_path).convert("RGB")
        mask_path_value = response.get("mask_image_path")
        mask_img = Image.open(mask_path_value).convert("L") if mask_path_value else None
        validation = validate_image_output("try_on", result_img, mask=mask_img)
        if not validation["passed"]:
            raise HTTPException(status_code=500, detail="; ".join(validation["failures"]))

        metadata = build_output_metadata(
            feature_key="try_on",
            output_path=output_path,
            parameters=_apply_processing_profile(payload).model_dump(),
            quality_validation=validation,
            capability_report=_get_capability_report(),
            extra={
                "mask_image_path": mask_path_value,
                "processing_profile": _apply_processing_profile(payload).processing_profile,
            },
        )
        sidecar_path = write_sidecar_metadata(output_path, metadata)
        response["quality_validation"] = validation
        response["metadata_path"] = str(sidecar_path)
        return JSONResponse(response)

    @fastapi_app.get("/api/tryon/setups")
    async def list_tryon_setups(cameraId: str | None = None, provider: str | None = None):
        """List selectable setups for a camera, newest local catalog state first.

        Pushes the local catalog into Atlas before reading it back, so the local file
        stays the source of truth for setup config while Camera reads metadata from
        Atlas. That write happens on every call — this endpoint is not read-only.

        Without `cameraId` only globally-scoped setups are returned; with one, that
        camera's setups are included too. Sort order is the selection order Camera
        shows: default first, then camera-specific, then rank, then name. `provider`
        filters to local or online ("cloud" is accepted as an alias).
        """
        camera_id = _normalize_opt_text(cameraId)
        provider_filter = _normalize_opt_text(provider)
        if provider_filter == "cloud":
            provider_filter = SETUP_PROVIDER_ONLINE
        if provider_filter and provider_filter not in {SETUP_PROVIDER_LOCAL, SETUP_PROVIDER_ONLINE}:
            raise HTTPException(status_code=400, detail="provider must be one of: local, online")
        client, db = _get_tryon_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Try-on MongoDB is not configured.")
        setup_collection_name, _ = _tryon_collection_names()
        local_setups = load_local_setups(_ROOT)
        _sync_tryon_setups_from_local_catalog(db, setup_collection_name, local_setups)
        query = {"active": True}
        if camera_id:
            query["$or"] = [
                {"cameraId": {"$exists": False}},
                {"cameraId": None},
                {"cameraId": camera_id},
            ]
        else:
            query["$or"] = [{"cameraId": {"$exists": False}}, {"cameraId": None}]
        if provider_filter:
            query["provider"] = provider_filter
        try:
            setups = []
            for setup in db[setup_collection_name].find(query).sort([("isDefault", -1), ("cameraId", 1), ("rank", 1), ("name", 1)]):
                setup_id = _normalize_opt_text(setup.get("setupId"))
                if not setup_id:
                    continue
                local_setup = local_setups.get(setup_id)
                if not local_setup:
                    continue
                config_payload = dict(local_setup.get("config") or {})
                resolved_provider = _normalize_opt_text(local_setup.get("provider")) or SETUP_PROVIDER_LOCAL
                setups.append(
                    {
                        "setupId": setup_id,
                        "name": local_setup.get("name") or setup.get("name") or setup_id,
                        "description": local_setup.get("description") or setup.get("description"),
                        "cameraId": _normalize_opt_text(local_setup.get("cameraId")) or _normalize_opt_text(setup.get("cameraId")),
                        "provider": resolved_provider,
                        "isDefault": bool(local_setup.get("isDefault")),
                        "rank": int(local_setup.get("rank") or setup.get("rank") or 0),
                        "revision": local_setup.get("revision"),
                        "config": config_payload,
                    }
                )
            return {"cameraId": camera_id, "setups": setups}
        finally:
            client.close()

    @fastapi_app.post("/api/tryon/setups/{setupId}/use")
    async def use_tryon_setup(setupId: str, payload: TryOnSetupSelectionRequest):
        setup_id = _normalize_opt_text(setupId)
        camera_id = _normalize_opt_text(payload.cameraId)
        if not setup_id:
            raise HTTPException(status_code=400, detail="setupId is required.")
        if not camera_id:
            raise HTTPException(status_code=400, detail="cameraId is required.")

        client, db = _get_tryon_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Try-on MongoDB is not configured.")
        setup_collection_name, preference_collection_name = _tryon_collection_names()
        local_setups = load_local_setups(_ROOT)
        local_setup = local_setups.get(setup_id)
        if not local_setup:
            raise HTTPException(status_code=404, detail="setupId not found in local setup catalog.")
        db[setup_collection_name].update_one(
            {"setupId": setup_id},
            {
                "$set": {
                    "setupId": setup_id,
                    "name": local_setup.get("name"),
                    "description": local_setup.get("description"),
                    "cameraId": local_setup.get("cameraId"),
                    "active": bool(local_setup.get("active", True)),
                    "isDefault": bool(local_setup.get("isDefault")),
                    "rank": int(local_setup.get("rank") or 0),
                    "revision": local_setup.get("revision"),
                    "provider": local_setup.get("provider") or SETUP_PROVIDER_LOCAL,
                    "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                },
                "$setOnInsert": {"createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
            },
            upsert=True,
        )
        updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        db[preference_collection_name].update_one(
            {"cameraId": camera_id},
            {"$set": {"setupId": setup_id, "cameraId": camera_id, "updatedAt": updated_at}},
            upsert=True,
        )
        try:
            return {
                "cameraId": camera_id,
                "setupId": setup_id,
                "updatedAt": updated_at,
            }
        finally:
            client.close()

    @fastapi_app.get("/api/capabilities")
    async def capabilities_api():
        return JSONResponse(_get_capability_report())

    @fastapi_app.get("/api/quality-contracts")
    async def quality_contracts_api():
        return JSONResponse(get_quality_contracts())

    @fastapi_app.get("/api/local-ai/services")
    async def local_ai_services_api():
        return JSONResponse(service_registry(_MODELS_ROOT))

    @fastapi_app.get("/api/local-ai/model-packs")
    async def local_ai_model_packs_api():
        return JSONResponse(evaluate_model_packs(_MODELS_ROOT))

    @fastapi_app.post("/api/local-ai/jobs")
    async def local_ai_job_api(payload: LocalAiJobRequest):
        try:
            result = run_local_ai_service(_ROOT, payload.serviceId, payload.payload)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return JSONResponse(result)

    @fastapi_app.post("/api/local-ai/garments/isolate")
    async def local_ai_garment_isolate_api(payload: dict[str, Any]):
        return JSONResponse(run_local_ai_service(_ROOT, "garment_isolation", payload))

    @fastapi_app.post("/api/local-ai/product-photo/cleanup")
    async def local_ai_product_cleanup_api(payload: dict[str, Any]):
        return JSONResponse(run_local_ai_service(_ROOT, "product_photo_cleanup", payload))

    @fastapi_app.post("/api/local-ai/quality/brand-safety")
    async def local_ai_brand_safety_api(payload: dict[str, Any]):
        return JSONResponse(run_local_ai_service(_ROOT, "brand_safety_analyzer", payload))

    @fastapi_app.post("/api/local-ai/quality/tryon-gate")
    async def local_ai_tryon_gate_api(payload: dict[str, Any]):
        return JSONResponse(run_local_ai_service(_ROOT, "tryon_quality_gate", payload))

    @fastapi_app.post("/api/local-ai/google-edge/analyze")
    async def local_ai_google_edge_api(payload: dict[str, Any]):
        return JSONResponse(run_local_ai_service(_ROOT, "google_edge_analyzer", payload))

    @fastapi_app.post("/api/local-ai/google-edge/tryon")
    async def local_ai_google_edge_tryon_api(payload: dict[str, Any]):
        return JSONResponse(run_local_ai_service(_ROOT, "google_edge_tryon", payload))

    @fastapi_app.post("/api/local-ai/editing/inpaint")
    async def local_ai_inpaint_api(payload: dict[str, Any]):
        return JSONResponse(run_local_ai_service(_ROOT, "local_inpainting_cleanup", payload))

    @fastapi_app.post("/api/local-ai/variants/generate")
    async def local_ai_variants_api(payload: dict[str, Any]):
        return JSONResponse(run_local_ai_service(_ROOT, "campaign_variant_generator", payload))

    @fastapi_app.post("/api/local-ai/events/{event_id}/social-stills")
    async def local_ai_event_stills_api(event_id: str, payload: dict[str, Any]):
        return JSONResponse(run_local_ai_service(_ROOT, "event_social_still_builder", {**payload, "eventId": event_id}))

    @fastapi_app.get("/api/local-ai/reports")
    async def local_ai_reports_api():
        return JSONResponse(run_local_ai_service(_ROOT, "local_ai_service_reporting", {}))

    @fastapi_app.get("/api/local-ai/reports/export")
    async def local_ai_reports_export_api():
        output_path = export_report_csv(_ROOT, _ROOT / ".runtime" / "local_ai" / "reports" / "local_ai_services.csv")
        return JSONResponse({"path": str(output_path)})

    @fastapi_app.get("/api/worker/status")
    async def worker_status_api():
        return JSONResponse(_build_worker_status_report())

    @fastapi_app.get("/api/worker/settings")
    async def worker_settings_api():
        return JSONResponse(load_worker_settings(app_root=_ROOT))

    @fastapi_app.post("/api/worker/settings")
    async def worker_settings_update_api(payload: WorkerSettingsRequest):
        normalized = normalize_worker_settings(
            {
                "enabled": payload.enabled,
                "pollIntervalSeconds": payload.pollIntervalSeconds,
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "updatedBy": payload.updatedBy or "local-operator",
            }
        )
        save_worker_settings(normalized, app_root=_ROOT)
        append_worker_event(
            {
                "jobId": None,
                "at": normalized["updatedAt"],
                "level": "info",
                "event": "worker_settings_updated",
                "status": "settings",
                "stage": "settings_updated",
                "details": {
                    "enabled": normalized["enabled"],
                    "pollIntervalSeconds": normalized["pollIntervalSeconds"],
                    "updatedBy": normalized["updatedBy"],
                },
            },
            app_root=_ROOT,
        )
        return JSONResponse(normalized)

    @fastapi_app.post("/api/worker/service-action")
    async def worker_service_action_api(payload: ServiceActionRequest):
        runtime_state = load_worker_status(app_root=_ROOT)
        current_job_id = runtime_state.get("currentJobId")
        normalized_action = payload.action.strip().lower()
        if current_job_id and normalized_action in {"restart", "run_now"} and _is_runtime_job_active(runtime_state):
            raise HTTPException(
                status_code=409,
                detail=f"Service action blocked while job {current_job_id} is active.",
            )
        try:
            result = perform_service_action(payload.target, normalized_action, app_root=_ROOT)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        requested_at = result["acceptedAt"]
        append_worker_event(
            {
                "jobId": None,
                "at": requested_at,
                "level": "info",
                "event": "service_action_requested",
                "status": "service_action",
                "stage": "service_action_requested",
                "details": {
                    "target": result["target"],
                    "action": result["action"],
                    "requestedBy": payload.requestedBy or "local-operator",
                },
            },
            app_root=_ROOT,
        )
        return JSONResponse(result)

    @fastapi_app.post("/api/worker/jobs/{job_id}/retry")
    async def worker_job_retry_api(job_id: str, payload: RetryWorkerJobRequest):
        """Re-queue a finished or failed job, clearing its error and lease state.

        Refuses (409) any job that is currently being worked — checked twice, against
        the local worker's runtime status and against the job's own status in Atlas,
        because a job claimed by another machine is invisible to the first check.
        Retrying a live job would let two workers publish results for one submission.

        `target` picks the landing status: "queued" for immediate pickup, or
        "retry_wait" with delayMinutes (0-1440) to hold it back. When resetAttempts is
        true the attempt count is zeroed (refilling the retry budget); otherwise it is
        left as-is. The response echoes resetAttempts.
        """
        runtime_state = load_worker_status(app_root=_ROOT)
        if str(runtime_state.get("currentJobId") or "") == job_id and _is_runtime_job_active(runtime_state):
            raise HTTPException(
                status_code=409,
                detail=f"Job {job_id} is currently active and cannot be retried.",
            )
        try:
            target_status = _normalize_retry_target(payload.target)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        delay_minutes = int(payload.delayMinutes or 0)
        if delay_minutes < 0:
            raise HTTPException(status_code=400, detail="delayMinutes must be >= 0.")
        if delay_minutes > 24 * 60:
            raise HTTPException(status_code=400, detail="delayMinutes must be <= 1440.")
        if target_status == "queued" and delay_minutes > 0:
            raise HTTPException(status_code=400, detail="delayMinutes is only supported when target is retry_wait.")

        client, db = _get_tryon_db()
        if db is None:
            raise HTTPException(status_code=503, detail="MongoDB Atlas configuration is unavailable.")

        requested_by = (payload.requestedBy or "local-operator").strip() or "local-operator"
        now = _now_utc_iso()
        try:
            job = db["tryon_jobs"].find_one({"jobId": job_id})
            if not job:
                raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

            current_status = str(job.get("status") or "").strip().lower()
            if current_status in {"claimed", "processing", "uploading_result", "notifying_camera"}:
                raise HTTPException(
                    status_code=409,
                    detail=f"Job {job_id} is in active status '{current_status}' and cannot be retried.",
                )
            if current_status and current_status not in {"queued", "retry_wait", "failed"}:
                raise HTTPException(
                    status_code=409,
                    detail=f"Job {job_id} is in non-retryable status '{current_status}'.",
                )

            next_attempt_at = _plus_minutes_iso(delay_minutes) if target_status == "retry_wait" else None
            update_fields: dict[str, Any] = {
                "status": target_status,
                "stage": "queued" if target_status == "queued" else "failed",
                "updatedAt": now,
                "error": {"code": None, "message": None, "details": None},
                "processing.leaseExpiresAt": None,
                "processing.finishedAt": None,
                "processing.lastHeartbeatAt": None,
                "processing.lastError": None,
                "processing.nextAttemptAt": next_attempt_at,
            }
            if payload.resetAttempts:
                update_fields["processing.attemptCount"] = 0

            db["tryon_jobs"].update_one(
                {"jobId": job_id},
                {
                    "$set": update_fields,
                    "$unset": {
                        "processing.publicationError": "",
                        "processing.startedAt": "",
                    },
                },
            )
            append_worker_event(
                {
                    "jobId": job_id,
                    "at": now,
                    "level": "info",
                    "event": "job_retried",
                    "status": target_status,
                    "stage": "retry_requested",
                    "details": {
                        "previousStatus": current_status or "",
                        "targetStatus": target_status,
                        "delayMinutes": delay_minutes,
                        "resetAttempts": bool(payload.resetAttempts),
                        "requestedBy": requested_by,
                    },
                },
                app_root=_ROOT,
            )
            return JSONResponse(
                {
                    "jobId": job_id,
                    "previousStatus": current_status or "",
                    "status": target_status,
                    "stage": "queued" if target_status == "queued" else "failed",
                    "nextAttemptAt": next_attempt_at,
                    "retryScheduled": target_status == "retry_wait",
                    "resetAttempts": bool(payload.resetAttempts),
                    "requestedBy": requested_by,
                    "updatedAt": now,
                }
            )
        finally:
            client.close()

    @fastapi_app.post("/api/tryon/jobs/{job_id}/retry")
    async def tryon_job_retry_api(job_id: str, payload: RetryWorkerJobRequest):
        return await worker_job_retry_api(job_id, payload)


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
    *,
    mask_mode="default",
):
    import traceback

    if resolution == "High Quality":
        num_steps = max(int(num_steps), 20)
        guidance = max(float(guidance), 3.0)
        if category == "Upper (T-Shirts, Hoodies)":
            mask_padding = max(int(mask_padding), 6)

    try:
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
            mask_mode=mask_mode,
        )
    except Exception as exc:
        print(f"[try-on] Inference failed: {exc}")
        traceback.print_exc()
        yield None, None, f"❌ Generation failed: {exc}", gr.update(), gr.update(interactive=True, value="Generate Try-On")


if __name__ == "__main__":
    threading.Thread(target=_load_models, daemon=True).start()
    demo = build_ui("generic")
    motogp_demo = build_ui("motogp")

    # Define the shared Gradio theme here so the mounted app surfaces stay visually consistent.
    # Theme tokens are more reliable than ad hoc CSS overrides for most Gradio-owned colors.
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
    # ponytail: the ops-banner/badge rules are inlined here (literal palette) because
    # the Gradio pages don't load global.css, so its CSS variables aren't available.
    gradio_extra_css = (
        "footer, .built-with-gradio, .pose-state-hidden { display: none !important; }"
        ".ops-banner{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 16px;"
        "margin-bottom:16px;border:1px solid #2a2a37;border-radius:8px;background:#181820;}"
        ".ops-banner-label{font-size:12px;text-transform:uppercase;letter-spacing:.5px;"
        "font-weight:700;color:#727169;}"
        ".badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;"
        "border:1px solid #3a3a4a;font-size:12px;font-weight:700;letter-spacing:.5px;"
        "text-transform:uppercase;color:#dcd7ba;background:#1f1f28;white-space:nowrap;}"
        ".badge::before{content:'';width:8px;height:8px;border-radius:50%;background:currentColor;}"
        ".badge--ok{color:#98bb6c;}.badge--warn{color:#dca561;}.badge--error{color:#e82424;}"
        ".badge--info{color:#b4befe;}.badge--neutral{color:#727169;}"
    )

    app = gr.mount_gradio_app(fastapi_app, demo, path="/try-on", theme=gradio_theme, css=gradio_extra_css)
    app = gr.mount_gradio_app(app, motogp_demo, path="/motogp-leather-magic", theme=gradio_theme, css=gradio_extra_css)

    uvicorn.run(app, host="127.0.0.1", port=7860)
