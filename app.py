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
from model_paths import (
    get_hf_home,
    get_models_root,
    load_settings as load_saved_settings,
    migrate_legacy_settings,
    save_settings as save_app_settings,
)

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
_MODELS_CONTROLNET_OPENPOSE = _MODELS_ROOT / "controlnet" / "sd15-openpose"
_MODELS_ANNOTATORS = _MODELS_ROOT / "processors" / "annotators"
_MODELS_IP_ADAPTER_FACEID = _MODELS_ROOT / "adapters" / "ip-adapter-faceid-sd15"
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
_PIPE    = None
_MASKER  = None
_ERROR   = None
_FACE_ENHANCER = None
_LOADED_VAE_TYPE = "hf" 
_GFPGAN_READY = False
_GFPGAN_ERROR = None
_INSIGHTFACE_ANALYZER = None
_INSIGHTFACE_SWAPPER = None
_INSIGHTFACE_ERROR = None
_HOLD_PRODUCT_INPAINTER = None
_HOLD_PRODUCT_ERROR = None
_HOLD_PRODUCT_DEVICE = None
_HOLD_PRODUCT_POSE_DETECTOR = None
_HOLD_PRODUCT_CONTROLNET = None
_HOLD_PRODUCT_PROPER_PIPE = None
_HOLD_PRODUCT_PROPER_ERROR = None
_HOLD_PRODUCT_PROPER_DEVICE = None
_READY   = threading.Event()

_INSIGHTFACE_ROOT = _MODELS_ROOT / "analysis" / "insightface"
_INSWAPPER_MODEL_NAME = "inswapper_128.onnx"
_HOLD_PRODUCT_POSE_TEMPLATES = {
    "None (Upload Your Own)": None,
    "Trophy Overhead": _ROOT / "images" / "test_theroad_girl_up.png",
    "Neutral Standing": _ROOT / "images" / "person_example.png",
}
_HOLD_PRODUCT_TEMPLATE_MODES = {
    "Trophy Overhead": "Overhead Trophy",
    "Neutral Standing": "Front Hold",
}
_POSE_EDITOR_JOINTS = [
    "nose",
    "neck",
    "right_shoulder",
    "right_elbow",
    "right_wrist",
    "left_shoulder",
    "left_elbow",
    "left_wrist",
    "right_hip",
    "left_hip",
]
_POSE_EDITOR_EDGES = [
    ("nose", "neck"),
    ("neck", "right_shoulder"),
    ("right_shoulder", "right_elbow"),
    ("right_elbow", "right_wrist"),
    ("neck", "left_shoulder"),
    ("left_shoulder", "left_elbow"),
    ("left_elbow", "left_wrist"),
    ("neck", "right_hip"),
    ("neck", "left_hip"),
]
_POSE_EDITOR_LABELS = {
    "nose": "Nose",
    "neck": "Neck",
    "right_shoulder": "R Shoulder",
    "right_elbow": "R Elbow",
    "right_wrist": "R Wrist",
    "left_shoulder": "L Shoulder",
    "left_elbow": "L Elbow",
    "left_wrist": "L Wrist",
    "right_hip": "R Hip",
    "left_hip": "L Hip",
}


def _insightface_providers(*, prefer_cpu: bool = False) -> list[str]:
    try:
        import onnxruntime as ort
        available = set(ort.get_available_providers())
    except Exception:
        return ["CPUExecutionProvider"]

    providers: list[str] = []
    if "CPUExecutionProvider" in available:
        providers.append("CPUExecutionProvider")
    if not prefer_cpu and "CoreMLExecutionProvider" in available:
        providers.insert(0, "CoreMLExecutionProvider")
    return providers or ["CPUExecutionProvider"]


def _select_primary_face(faces):
    if not faces:
        return None
    return max(
        faces,
        key=lambda face: float((face.bbox[2] - face.bbox[0]) * (face.bbox[3] - face.bbox[1])),
    )


def _bbox_area(bbox) -> float:
    return max(0.0, float(bbox[2] - bbox[0])) * max(0.0, float(bbox[3] - bbox[1]))


def _expand_bbox(bbox, image_shape, *, scale=2.8, shift_y=-0.15):
    x0, y0, x1, y1 = [float(v) for v in bbox]
    h, w = image_shape[:2]
    bw = x1 - x0
    bh = y1 - y0
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0 + bh * shift_y
    new_w = bw * scale
    new_h = bh * scale
    nx0 = max(0, int(round(cx - new_w / 2.0)))
    ny0 = max(0, int(round(cy - new_h / 2.0)))
    nx1 = min(w, int(round(cx + new_w / 2.0)))
    ny1 = min(h, int(round(cy + new_h / 2.0)))
    if nx1 <= nx0 or ny1 <= ny0:
        return None
    return (nx0, ny0, nx1, ny1)


def _build_face_blend_mask(face, image_shape):
    import cv2
    import numpy as np

    h, w = image_shape[:2]
    mask = np.zeros((h, w), dtype=np.float32)
    bbox = face.bbox.astype(np.float32)
    x0, y0, x1, y1 = bbox
    bw = max(1.0, x1 - x0)
    bh = max(1.0, y1 - y0)

    center = (int(round((x0 + x1) / 2.0)), int(round(y0 + bh * 0.52)))
    axes = (max(1, int(round(bw * 0.48))), max(1, int(round(bh * 0.68))))
    cv2.ellipse(mask, center, axes, 0, 0, 360, 1.0, -1)

    if getattr(face, "kps", None) is not None:
        kps = np.asarray(face.kps, dtype=np.float32)
        if kps.shape[0] >= 5:
            jaw_top = max(0, int(round(min(kps[:, 1]) - 0.10 * bh)))
            jaw_bottom = min(h, int(round(max(kps[:, 1]) + 0.85 * bh)))
            jaw_left = max(0, int(round(min(kps[:, 0]) - 0.35 * bw)))
            jaw_right = min(w, int(round(max(kps[:, 0]) + 0.35 * bw)))
            jaw_poly = np.array(
                [
                    [jaw_left, jaw_top + int(0.12 * bh)],
                    [jaw_right, jaw_top + int(0.12 * bh)],
                    [int(round(center[0] + 0.34 * bw)), jaw_bottom],
                    [int(round(center[0] - 0.34 * bw)), jaw_bottom],
                ],
                dtype=np.int32,
            )
            cv2.fillConvexPoly(mask, jaw_poly, 1.0)

    # Soften the lower face transition more than the forehead to hide the neck seam.
    fade = np.linspace(1.0, 0.35, h, dtype=np.float32).reshape(h, 1)
    mask *= fade
    blur = max(9, int(round(max(bw, bh) * 0.12)))
    if blur % 2 == 0:
        blur += 1
    mask = cv2.GaussianBlur(mask, (blur, blur), 0)
    return np.clip(mask, 0.0, 1.0)


def _match_face_color(swapped_bgr, target_bgr, mask):
    import numpy as np

    active = mask > 0.05
    if active.sum() < 32:
        return swapped_bgr

    out = swapped_bgr.astype(np.float32).copy()
    target = target_bgr.astype(np.float32)
    source = swapped_bgr.astype(np.float32)
    for channel in range(3):
        src_vals = source[:, :, channel][active]
        tgt_vals = target[:, :, channel][active]
        src_mean = float(src_vals.mean())
        src_std = float(src_vals.std())
        tgt_mean = float(tgt_vals.mean())
        tgt_std = float(tgt_vals.std())
        if src_std < 1e-5:
            adjusted = src_vals - src_mean + tgt_mean
        else:
            adjusted = ((src_vals - src_mean) / src_std) * max(tgt_std, 1e-5) + tgt_mean
        chan = out[:, :, channel]
        chan[active] = adjusted
        out[:, :, channel] = chan
    return np.clip(out, 0, 255).astype(np.uint8)


def _refine_swapped_face(swapped_bgr, target_bgr, face):
    import numpy as np

    mask = _build_face_blend_mask(face, target_bgr.shape)
    color_matched = _match_face_color(swapped_bgr, target_bgr, mask)
    alpha = np.repeat(mask[:, :, None], 3, axis=2)
    refined = alpha * color_matched.astype(np.float32) + (1.0 - alpha) * target_bgr.astype(np.float32)
    return np.clip(refined, 0, 255).astype(np.uint8)


def _to_pil_image(image):
    from PIL import Image
    import numpy as np

    if isinstance(image, Image.Image):
        return image
    return Image.fromarray(np.asarray(image))


def _remove_product_background(product_pil, *, threshold: int, edge_softness: int):
    import numpy as np
    from PIL import Image, ImageFilter

    rgba = product_pil.convert("RGBA")
    alpha = np.array(rgba.getchannel("A"))
    if alpha.max() > 0 and alpha.min() < 255:
        return rgba

    rgb = np.array(rgba.convert("RGB")).astype(np.int16)
    h, w = rgb.shape[:2]
    patch = max(4, min(h, w) // 12)
    corners = np.concatenate(
        [
            rgb[:patch, :patch].reshape(-1, 3),
            rgb[:patch, -patch:].reshape(-1, 3),
            rgb[-patch:, :patch].reshape(-1, 3),
            rgb[-patch:, -patch:].reshape(-1, 3),
        ],
        axis=0,
    )
    bg_color = np.median(corners, axis=0)
    distance = np.linalg.norm(rgb - bg_color.reshape(1, 1, 3), axis=2)
    mask = (distance > float(threshold)).astype(np.uint8) * 255
    mask_img = Image.fromarray(mask, mode="L")
    blur = max(1, int(edge_softness))
    if blur > 0:
        mask_img = mask_img.filter(ImageFilter.GaussianBlur(radius=blur))
    rgba.putalpha(mask_img)
    return rgba


def _prepare_hold_product_composite(
    person_pil,
    product_pil,
    *,
    center_x: float,
    center_y: float,
    scale: float,
    rotation: float,
    opacity: float,
    shadow_strength: float,
    shadow_blur: int,
    auto_remove_bg: bool,
    bg_threshold: int,
    edge_softness: int,
):
    from PIL import Image, ImageChops, ImageEnhance, ImageFilter

    person = _to_pil_image(person_pil).convert("RGB")
    product = _to_pil_image(product_pil)
    product_rgba = (
        _remove_product_background(product, threshold=bg_threshold, edge_softness=edge_softness)
        if auto_remove_bg
        else product.convert("RGBA")
    )

    base_w, base_h = person.size
    target_w = max(24, int(base_w * float(scale)))
    aspect = product_rgba.height / max(product_rgba.width, 1)
    target_h = max(24, int(target_w * aspect))
    product_rgba = product_rgba.resize((target_w, target_h), Image.LANCZOS)
    if rotation:
        product_rgba = product_rgba.rotate(float(rotation), resample=Image.BICUBIC, expand=True)

    if opacity < 1.0:
        alpha = product_rgba.getchannel("A")
        alpha = ImageEnhance.Brightness(alpha).enhance(float(opacity))
        product_rgba.putalpha(alpha)

    cx = int(base_w * float(center_x))
    cy = int(base_h * float(center_y))
    px = cx - product_rgba.width // 2
    py = cy - product_rgba.height // 2

    result = person.convert("RGBA")

    if shadow_strength > 0:
        shadow_alpha = product_rgba.getchannel("A")
        shadow_alpha = ImageEnhance.Brightness(shadow_alpha).enhance(float(shadow_strength))
        shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(radius=max(1, int(shadow_blur))))
        shadow = Image.new("RGBA", result.size, (0, 0, 0, 0))
        shadow_layer = Image.new("RGBA", product_rgba.size, (0, 0, 0, 0))
        shadow_layer.putalpha(shadow_alpha)
        shadow.paste(shadow_layer, (px + 6, py + 8), shadow_layer)
        result = Image.alpha_composite(result, shadow)

    layer = Image.new("RGBA", result.size, (0, 0, 0, 0))
    layer.paste(product_rgba, (px, py), product_rgba)
    result = Image.alpha_composite(result, layer)
    alpha_canvas = Image.new("L", result.size, 0)
    alpha_canvas.paste(product_rgba.getchannel("A"), (px, py))
    bbox = (
        max(0, px),
        max(0, py),
        min(result.width, px + product_rgba.width),
        min(result.height, py + product_rgba.height),
    )
    return {
        "composite": result.convert("RGB"),
        "person": person,
        "product_rgba": product_rgba,
        "product_alpha": alpha_canvas,
        "bbox": bbox,
        "placement": (px, py),
    }


def _compose_hold_product(
    person_img,
    product_img,
    *,
    center_x: float,
    center_y: float,
    scale: float,
    rotation: float,
    opacity: float,
    shadow_strength: float,
    shadow_blur: int,
    auto_remove_bg: bool,
    bg_threshold: int,
    edge_softness: int,
):
    prepared = _prepare_hold_product_composite(
        person_img,
        product_img,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        rotation=rotation,
        opacity=opacity,
        shadow_strength=shadow_strength,
        shadow_blur=shadow_blur,
        auto_remove_bg=auto_remove_bg,
        bg_threshold=bg_threshold,
        edge_softness=edge_softness,
    )
    return prepared["composite"]


def _build_hold_product_inpaint_masks(
    *,
    canvas_size: tuple[int, int],
    product_alpha,
    bbox: tuple[int, int, int, int],
    hold_style: str,
    contact_strength: float,
):
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    width, height = canvas_size
    x0, y0, x1, y1 = bbox
    box_w = max(1, x1 - x0)
    box_h = max(1, y1 - y0)
    base_expand = max(8, int(min(box_w, box_h) * (0.08 + 0.12 * float(contact_strength))))
    ring_expand = max(3, base_expand // 2)
    core_erode = max(5, int(min(box_w, box_h) * 0.04))

    dilated = product_alpha.filter(ImageFilter.MaxFilter(size=base_expand * 2 + 1))
    eroded = product_alpha.filter(ImageFilter.MinFilter(size=max(3, core_erode * 2 + 1)))
    ring = ImageChops.subtract(dilated, eroded)

    contact = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(contact)
    hand_y = y0 + int(box_h * 0.56)
    top_y = y0 + int(box_h * 0.14)
    left_cx = x0 + int(box_w * 0.18)
    right_cx = x0 + int(box_w * 0.82)
    bottom_cx = x0 + box_w // 2
    side_rx = max(18, int(box_w * 0.22))
    side_ry = max(18, int(box_h * 0.22))
    bottom_rx = max(20, int(box_w * 0.28))
    bottom_ry = max(14, int(box_h * 0.16))
    top_rx = max(16, int(box_w * 0.18))
    top_ry = max(16, int(box_h * 0.14))

    def _ellipse(cx: int, cy: int, rx: int, ry: int):
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)

    if hold_style == "Two Hands Overhead Trophy":
        _ellipse(left_cx, top_y, top_rx, top_ry)
        _ellipse(right_cx, top_y, top_rx, top_ry)
    elif hold_style == "Left Hand Grip":
        _ellipse(left_cx, hand_y, side_rx, side_ry)
    elif hold_style == "Right Hand Grip":
        _ellipse(right_cx, hand_y, side_rx, side_ry)
    elif hold_style == "Bottom Cradle":
        _ellipse(bottom_cx, y0 + int(box_h * 0.84), bottom_rx, bottom_ry)
    else:
        _ellipse(left_cx, hand_y, side_rx, side_ry)
        _ellipse(right_cx, hand_y, side_rx, side_ry)
        _ellipse(bottom_cx, y0 + int(box_h * 0.80), bottom_rx, bottom_ry)

    bbox_fill = Image.new("L", (width, height), 0)
    bbox_draw = ImageDraw.Draw(bbox_fill)
    bbox_draw.rounded_rectangle(
        (
            max(0, x0 - ring_expand),
            max(0, y0 - ring_expand),
            min(width, x1 + ring_expand),
            min(height, y1 + ring_expand),
        ),
        radius=max(12, ring_expand * 2),
        fill=180,
    )

    inpaint_mask = ImageChops.lighter(ring, contact)
    inpaint_mask = ImageChops.lighter(inpaint_mask, bbox_fill)
    inpaint_mask = inpaint_mask.filter(ImageFilter.GaussianBlur(radius=max(4, ring_expand)))

    preserve_mask = product_alpha.filter(ImageFilter.MinFilter(size=max(3, core_erode * 2 + 1)))
    preserve_mask = preserve_mask.filter(ImageFilter.GaussianBlur(radius=max(2, core_erode // 2)))
    return inpaint_mask, preserve_mask


def _resize_for_sd(image, *, max_side: int = 1024, min_side: int = 512):
    from PIL import Image

    width, height = image.size
    scale = min(max_side / max(width, height), 1.0)
    scaled_w = max(8, int(round(width * scale / 8) * 8))
    scaled_h = max(8, int(round(height * scale / 8) * 8))

    if min(scaled_w, scaled_h) < min_side:
        upscale = min_side / float(min(scaled_w, scaled_h))
        scaled_w = max(8, int(round(scaled_w * upscale / 8) * 8))
        scaled_h = max(8, int(round(scaled_h * upscale / 8) * 8))

    if (scaled_w, scaled_h) == image.size:
        return image, (width, height)
    return image.resize((scaled_w, scaled_h), Image.LANCZOS), (width, height)


def _ensure_hold_product_inpainter(*, force_device: str | None = None):
    global _HOLD_PRODUCT_INPAINTER, _HOLD_PRODUCT_ERROR, _HOLD_PRODUCT_DEVICE
    requested_device = force_device or _preferred_device()
    if _HOLD_PRODUCT_INPAINTER is not None and _HOLD_PRODUCT_DEVICE == requested_device:
        return _HOLD_PRODUCT_INPAINTER
    if _HOLD_PRODUCT_ERROR and _HOLD_PRODUCT_DEVICE == requested_device:
        raise RuntimeError(_HOLD_PRODUCT_ERROR)

    try:
        from diffusers import EulerAncestralDiscreteScheduler, StableDiffusionInpaintPipeline
        from diffusers import AutoencoderKL

        _require_path(_MODELS_SD, label="Stable Diffusion inpainting checkpoint")
        device = requested_device
        # SD15 inpaint on Apple Silicon is noticeably more stable in float32.
        dtype = torch.float32

        pipe = StableDiffusionInpaintPipeline.from_pretrained(
            str(_MODELS_SD),
            torch_dtype=dtype,
            local_files_only=True,
            use_safetensors=False,
            safety_checker=None,
            requires_safety_checker=False,
        )
        if _MODELS_VAE.exists():
            pipe.vae = AutoencoderKL.from_pretrained(
                str(_MODELS_VAE),
                torch_dtype=dtype,
                local_files_only=True,
                use_safetensors=False,
            )
        pipe.scheduler = EulerAncestralDiscreteScheduler.from_config(pipe.scheduler.config)
        pipe.set_progress_bar_config(disable=True)
        if hasattr(pipe, "enable_attention_slicing"):
            pipe.enable_attention_slicing()
        if hasattr(pipe, "enable_vae_slicing"):
            pipe.enable_vae_slicing()
        pipe = pipe.to(device)
        _HOLD_PRODUCT_INPAINTER = pipe
        _HOLD_PRODUCT_DEVICE = device
        _HOLD_PRODUCT_ERROR = None
        return pipe
    except Exception as exc:
        _HOLD_PRODUCT_ERROR = str(exc)
        _HOLD_PRODUCT_DEVICE = requested_device
        raise RuntimeError(str(exc)) from exc


def _generate_hold_product(
    person_img,
    product_img,
    *,
    center_x: float,
    center_y: float,
    scale: float,
    rotation: float,
    opacity: float,
    shadow_strength: float,
    shadow_blur: int,
    auto_remove_bg: bool,
    bg_threshold: int,
    edge_softness: int,
    hold_style: str,
    product_description: str,
    custom_prompt: str,
    contact_strength: float,
    preserve_product_detail: bool,
    steps: int,
    guidance: float,
    seed: int,
):
    import numpy as np
    from PIL import Image

    prepared = _prepare_hold_product_composite(
        person_img,
        product_img,
        center_x=center_x,
        center_y=center_y,
        scale=scale,
        rotation=rotation,
        opacity=opacity,
        shadow_strength=shadow_strength,
        shadow_blur=shadow_blur,
        auto_remove_bg=auto_remove_bg,
        bg_threshold=bg_threshold,
        edge_softness=edge_softness,
    )
    init_image = prepared["composite"]
    inpaint_mask, preserve_mask = _build_hold_product_inpaint_masks(
        canvas_size=init_image.size,
        product_alpha=prepared["product_alpha"],
        bbox=prepared["bbox"],
        hold_style=hold_style,
        contact_strength=contact_strength,
    )

    if custom_prompt and custom_prompt.strip():
        prompt = custom_prompt.strip()
    elif hold_style == "Two Hands Overhead Trophy":
        prompt = (
            "photorealistic person lifting "
            f"{product_description.strip() or 'the product'} above their head with both hands like a trophy, "
            "realistic fingers gripping the side handles or upper edges, natural arm extension, "
            "strong contact shadows, coherent lighting, celebratory trophy-lift pose, high detail"
        )
    else:
        prompt = (
            "photorealistic person naturally holding "
            f"{product_description.strip() or 'the product'} with realistic hand grip, "
            "fingers wrapped around the object, natural occlusion, realistic contact shadows, "
            "coherent lighting, product centered in the hands, high detail"
        )
    negative_prompt = (
        "floating object, duplicated product, extra arms, extra hands, missing fingers, "
        "deformed hands, melted fingers, bad anatomy, blurry, cartoon, CGI, cut off object, watermark"
    )

    resized_init, original_size = _resize_for_sd(init_image)
    resized_mask, _ = _resize_for_sd(inpaint_mask)
    resized_preserve, _ = _resize_for_sd(preserve_mask)

    def _run_inpaint(pass_device: str):
        pipe = _ensure_hold_product_inpainter(force_device=pass_device)
        generator_device = getattr(pipe, "_execution_device", pass_device)
        try:
            generator = torch.Generator(device=generator_device).manual_seed(int(seed))
        except Exception:
            generator = torch.Generator(device="cpu").manual_seed(int(seed))
        return pipe(
            prompt=prompt,
            negative_prompt=negative_prompt,
            image=resized_init,
            mask_image=resized_mask,
            num_inference_steps=int(steps),
            guidance_scale=float(guidance),
            strength=0.99,
            generator=generator,
        ).images[0]

    active_device = _preferred_device()
    result = _run_inpaint(active_device)

    result_arr = np.asarray(result.convert("RGB"), dtype=np.float32)
    if active_device == "mps" and float(result_arr.mean()) < 2.0:
        # MPS occasionally returns an almost-black frame from SD15 inpaint.
        # Retry once on CPU rather than returning a broken image.
        global _HOLD_PRODUCT_INPAINTER, _HOLD_PRODUCT_DEVICE, _HOLD_PRODUCT_ERROR
        _HOLD_PRODUCT_INPAINTER = None
        _HOLD_PRODUCT_DEVICE = None
        _HOLD_PRODUCT_ERROR = None
        result = _run_inpaint("cpu")

    if preserve_product_detail:
        result = Image.composite(resized_init, result, resized_preserve)

    if result.size != original_size:
        result = result.resize(original_size, Image.LANCZOS)
    if inpaint_mask.size != original_size:
        inpaint_mask = inpaint_mask.resize(original_size, Image.LANCZOS)
    return result, inpaint_mask


def _hold_pose_keypoint_xy(keypoint, width: int, height: int):
    if keypoint is None:
        return None
    if getattr(keypoint, "x", None) is None or getattr(keypoint, "y", None) is None:
        return None
    if keypoint.x < 0 or keypoint.y < 0:
        return None
    return (float(keypoint.x) * width, float(keypoint.y) * height)


def _select_primary_pose_result(poses, width: int, height: int):
    best_pose = None
    best_score = -1.0
    for pose in poses or []:
        pts = [_hold_pose_keypoint_xy(kp, width, height) for kp in pose.body.keypoints if kp is not None]
        pts = [pt for pt in pts if pt is not None]
        if len(pts) < 4:
            continue
        xs = [pt[0] for pt in pts]
        ys = [pt[1] for pt in pts]
        area = max(1.0, (max(xs) - min(xs)) * (max(ys) - min(ys)))
        score = area * max(1.0, float(getattr(pose.body, "total_score", 1.0)))
        if score > best_score:
            best_score = score
            best_pose = pose
    return best_pose


def _ensure_hold_product_proper_pipeline(*, force_device: str | None = None):
    global _HOLD_PRODUCT_POSE_DETECTOR, _HOLD_PRODUCT_CONTROLNET, _HOLD_PRODUCT_PROPER_PIPE
    global _HOLD_PRODUCT_PROPER_ERROR, _HOLD_PRODUCT_PROPER_DEVICE

    requested_device = force_device or _preferred_device()
    if _HOLD_PRODUCT_PROPER_PIPE is not None and _HOLD_PRODUCT_PROPER_DEVICE == requested_device:
        return _HOLD_PRODUCT_POSE_DETECTOR, _HOLD_PRODUCT_CONTROLNET, _HOLD_PRODUCT_PROPER_PIPE
    if _HOLD_PRODUCT_PROPER_ERROR and _HOLD_PRODUCT_PROPER_DEVICE == requested_device:
        raise RuntimeError(_HOLD_PRODUCT_PROPER_ERROR)

    try:
        from controlnet_aux import OpenposeDetector
        from diffusers import AutoencoderKL, ControlNetModel, StableDiffusionControlNetInpaintPipeline
        from diffusers import EulerAncestralDiscreteScheduler

        _require_path(_MODELS_SD, label="Stable Diffusion inpainting checkpoint")
        _require_path(_MODELS_CONTROLNET_OPENPOSE, label="ControlNet OpenPose checkpoint")
        _require_path(_MODELS_ANNOTATORS, label="OpenPose annotators")

        device = requested_device
        dtype = torch.float32
        pose_detector = OpenposeDetector.from_pretrained(str(_MODELS_ANNOTATORS), local_files_only=True)
        controlnet = ControlNetModel.from_pretrained(
            str(_MODELS_CONTROLNET_OPENPOSE),
            torch_dtype=dtype,
            local_files_only=True,
            use_safetensors=False,
        )
        pipe = StableDiffusionControlNetInpaintPipeline.from_pretrained(
            str(_MODELS_SD),
            controlnet=controlnet,
            torch_dtype=dtype,
            local_files_only=True,
            use_safetensors=False,
            safety_checker=None,
            requires_safety_checker=False,
        )
        if _MODELS_VAE.exists():
            pipe.vae = AutoencoderKL.from_pretrained(
                str(_MODELS_VAE),
                torch_dtype=dtype,
                local_files_only=True,
                use_safetensors=False,
            )
        pipe.scheduler = EulerAncestralDiscreteScheduler.from_config(pipe.scheduler.config)
        pipe.set_progress_bar_config(disable=True)
        if hasattr(pipe, "enable_attention_slicing"):
            pipe.enable_attention_slicing()
        if hasattr(pipe.vae, "enable_slicing"):
            pipe.vae.enable_slicing()
        pipe = pipe.to(device)

        _HOLD_PRODUCT_POSE_DETECTOR = pose_detector
        _HOLD_PRODUCT_CONTROLNET = controlnet
        _HOLD_PRODUCT_PROPER_PIPE = pipe
        _HOLD_PRODUCT_PROPER_DEVICE = device
        _HOLD_PRODUCT_PROPER_ERROR = None
        return pose_detector, controlnet, pipe
    except Exception as exc:
        _HOLD_PRODUCT_PROPER_ERROR = str(exc)
        _HOLD_PRODUCT_PROPER_DEVICE = requested_device
        raise RuntimeError(str(exc)) from exc


def _extract_pose_map_and_result(pose_image_pil):
    import numpy as np

    pose_detector, _, _ = _ensure_hold_product_proper_pipeline()
    pose_np = np.array(pose_image_pil.convert("RGB"))
    poses = pose_detector.detect_poses(pose_np, include_hand=False, include_face=False)
    primary_pose = _select_primary_pose_result(poses, pose_image_pil.width, pose_image_pil.height)
    if primary_pose is None:
        raise ValueError("No body pose detected in pose reference image.")
    pose_map = pose_detector(
        pose_np,
        detect_resolution=768,
        image_resolution=max(pose_image_pil.width, pose_image_pil.height),
        include_body=True,
        include_hand=False,
        include_face=False,
        output_type="pil",
    )
    return pose_map, primary_pose


def _estimate_product_bbox_from_pose(
    pose_result,
    image_size: tuple[int, int],
    product_aspect: float,
    *,
    hold_mode: str,
    x_offset: float,
    y_offset: float,
    scale_multiplier: float,
):
    width, height = image_size
    body = pose_result.body.keypoints
    # OpenPose body indexing: 2/3/4 = right shoulder/elbow/wrist, 5/6/7 = left shoulder/elbow/wrist
    right_wrist = _hold_pose_keypoint_xy(body[4] if len(body) > 4 else None, width, height)
    left_wrist = _hold_pose_keypoint_xy(body[7] if len(body) > 7 else None, width, height)
    right_shoulder = _hold_pose_keypoint_xy(body[2] if len(body) > 2 else None, width, height)
    left_shoulder = _hold_pose_keypoint_xy(body[5] if len(body) > 5 else None, width, height)

    hand_points = [pt for pt in (left_wrist, right_wrist) if pt is not None]
    shoulder_points = [pt for pt in (left_shoulder, right_shoulder) if pt is not None]
    if not hand_points:
        raise ValueError("Could not detect wrist positions in pose reference.")

    if len(hand_points) == 2:
        hands_mid_x = (hand_points[0][0] + hand_points[1][0]) / 2.0
        hands_mid_y = (hand_points[0][1] + hand_points[1][1]) / 2.0
        wrist_distance = abs(hand_points[0][0] - hand_points[1][0])
    else:
        hands_mid_x, hands_mid_y = hand_points[0]
        wrist_distance = width * 0.16

    if shoulder_points:
        shoulder_span = abs(shoulder_points[0][0] - shoulder_points[-1][0]) if len(shoulder_points) == 2 else width * 0.18
        shoulder_y = sum(pt[1] for pt in shoulder_points) / len(shoulder_points)
    else:
        shoulder_span = width * 0.18
        shoulder_y = hands_mid_y + height * 0.15

    base_width = max(shoulder_span * 0.55, wrist_distance * (0.95 if hold_mode == "Overhead Trophy" else 0.8), width * 0.12)
    target_w = max(56, int(base_width * float(scale_multiplier)))
    target_h = max(72, int(target_w * product_aspect))

    if hold_mode == "Overhead Trophy":
        center_x = hands_mid_x
        center_y = min(pt[1] for pt in hand_points) + target_h * 0.32
    else:
        center_x = hands_mid_x
        center_y = shoulder_y + target_h * 0.75

    center_x += width * float(x_offset)
    center_y += height * float(y_offset)

    x0 = int(round(center_x - target_w / 2))
    y0 = int(round(center_y - target_h / 2))
    x1 = x0 + target_w
    y1 = y0 + target_h
    return (x0, y0, x1, y1), {"left_wrist": left_wrist, "right_wrist": right_wrist}


def _compose_product_at_bbox(
    base_pil,
    product_pil,
    *,
    bbox: tuple[int, int, int, int],
    auto_remove_bg: bool,
    bg_threshold: int,
    edge_softness: int,
):
    from PIL import Image

    base = _to_pil_image(base_pil).convert("RGB")
    product = _to_pil_image(product_pil)
    product_rgba = (
        _remove_product_background(product, threshold=bg_threshold, edge_softness=edge_softness)
        if auto_remove_bg
        else product.convert("RGBA")
    )
    x0, y0, x1, y1 = bbox
    target_w = max(8, x1 - x0)
    target_h = max(8, y1 - y0)
    product_rgba = product_rgba.resize((target_w, target_h), Image.LANCZOS)

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(product_rgba, (x0, y0), product_rgba)
    composite = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")

    alpha_canvas = Image.new("L", base.size, 0)
    alpha_canvas.paste(product_rgba.getchannel("A"), (x0, y0))
    safe_bbox = (
        max(0, x0),
        max(0, y0),
        min(base.width, x0 + target_w),
        min(base.height, y0 + target_h),
    )
    return {
        "composite": composite,
        "product_alpha": alpha_canvas,
        "product_rgba": product_rgba,
        "bbox": safe_bbox,
    }


def _estimate_product_grip_anchors(product_rgba):
    import numpy as np

    alpha = np.array(product_rgba.getchannel("A"))
    ys, xs = np.where(alpha > 8)
    if len(xs) == 0:
        w, h = product_rgba.size
        return (w * 0.2, h * 0.18), (w * 0.8, h * 0.18)

    w, h = product_rgba.size
    upper_mask = ys < int(h * 0.34)

    def _anchor(side: str):
        if side == "left":
            side_mask = xs < int(w * 0.40)
        else:
            side_mask = xs > int(w * 0.60)
        mask = upper_mask & side_mask
        if not np.any(mask):
            mask = side_mask
        if not np.any(mask):
            mask = np.ones_like(xs, dtype=bool)
        cand_x = xs[mask]
        cand_y = ys[mask]
        if side == "left":
            edge_x = np.percentile(cand_x, 12)
            edge_mask = cand_x <= edge_x + max(4, int(w * 0.02))
        else:
            edge_x = np.percentile(cand_x, 88)
            edge_mask = cand_x >= edge_x - max(4, int(w * 0.02))
        if not np.any(edge_mask):
            edge_mask = np.ones_like(cand_x, dtype=bool)
        edge_xs = cand_x[edge_mask]
        edge_ys = cand_y[edge_mask]
        top_y = np.percentile(edge_ys, 18)
        top_mask = edge_ys <= top_y + max(4, int(h * 0.02))
        if not np.any(top_mask):
            top_mask = np.ones_like(edge_ys, dtype=bool)
        return float(edge_xs[top_mask].mean()), float(edge_ys[top_mask].mean())

    return _anchor("left"), _anchor("right")


def _pose_point_distance(a, b):
    if a is None or b is None:
        return None
    dx = float(a[0]) - float(b[0])
    dy = float(a[1]) - float(b[1])
    return (dx * dx + dy * dy) ** 0.5


def _pose_midpoint(a, b):
    if a is None or b is None:
        return None
    return ((float(a[0]) + float(b[0])) / 2.0, (float(a[1]) + float(b[1])) / 2.0)


def _compose_product_between_wrists(
    base_pil,
    product_pil,
    *,
    wrists: dict[str, tuple[float, float] | None],
    pose_points: dict[str, tuple[float, float] | None] | None,
    x_offset: float,
    y_offset: float,
    scale_multiplier: float,
    auto_remove_bg: bool,
    bg_threshold: int,
    edge_softness: int,
):
    from PIL import Image

    base = _to_pil_image(base_pil).convert("RGB")
    product = _to_pil_image(product_pil)
    product_rgba = (
        _remove_product_background(product, threshold=bg_threshold, edge_softness=edge_softness)
        if auto_remove_bg
        else product.convert("RGBA")
    )

    left_wrist = wrists.get("left_wrist")
    right_wrist = wrists.get("right_wrist")
    if left_wrist is None or right_wrist is None:
        raise ValueError("Both wrists are required for anchored overhead product placement.")

    left_anchor, right_anchor = _estimate_product_grip_anchors(product_rgba)
    anchor_distance = max(1.0, _pose_point_distance(left_anchor, right_anchor) or 1.0)
    wrist_distance = max(24.0, _pose_point_distance(left_wrist, right_wrist) or 24.0)
    scale = (wrist_distance / anchor_distance) * float(scale_multiplier)
    target_w = max(16, int(round(product_rgba.width * scale)))
    target_h = max(16, int(round(product_rgba.height * scale)))

    shoulder_span = None
    torso_height = None
    nose_y = None
    if pose_points:
        shoulder_span = _pose_point_distance(pose_points.get("left_shoulder"), pose_points.get("right_shoulder"))
        torso_mid = _pose_midpoint(pose_points.get("left_hip"), pose_points.get("right_hip"))
        torso_height = _pose_point_distance(pose_points.get("neck"), torso_mid)
        nose = pose_points.get("nose")
        neck = pose_points.get("neck")
        if nose is not None and neck is not None:
            nose_y = min(float(nose[1]), float(neck[1]))

    max_w = min(
        float(base.width) * 0.52,
        max(
            96.0,
            (shoulder_span or (base.width * 0.22)) * 1.65,
            wrist_distance * 1.08,
        ),
    )
    max_h = min(
        float(base.height) * 0.52,
        max(
            128.0,
            (torso_height or (base.height * 0.22)) * 1.55,
            max_w * (product_rgba.height / max(product_rgba.width, 1)),
        ),
    )
    width_ratio = max_w / max(float(target_w), 1.0)
    height_ratio = max_h / max(float(target_h), 1.0)
    scale *= min(1.0, width_ratio, height_ratio)
    target_w = max(16, int(round(product_rgba.width * scale)))
    target_h = max(16, int(round(product_rgba.height * scale)))
    product_rgba = product_rgba.resize((target_w, target_h), Image.LANCZOS)

    left_anchor = (left_anchor[0] * scale, left_anchor[1] * scale)
    right_anchor = (right_anchor[0] * scale, right_anchor[1] * scale)
    anchor_mid = _pose_midpoint(left_anchor, right_anchor)
    wrist_mid = _pose_midpoint(left_wrist, right_wrist)
    if anchor_mid is None or wrist_mid is None:
        raise ValueError("Failed to compute anchored overhead placement.")

    x0 = int(round(wrist_mid[0] - anchor_mid[0] + base.width * float(x_offset)))
    y0 = int(round(wrist_mid[1] - anchor_mid[1] + base.height * float(y_offset)))

    if nose_y is not None:
        face_clearance = max(18.0, (torso_height or (base.height * 0.18)) * 0.18)
        max_bottom = nose_y - face_clearance
        current_bottom = y0 + product_rgba.height
        if current_bottom > max_bottom:
            y0 -= int(round(current_bottom - max_bottom))

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(product_rgba, (x0, y0), product_rgba)
    composite = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")

    alpha_canvas = Image.new("L", base.size, 0)
    alpha_canvas.paste(product_rgba.getchannel("A"), (x0, y0))
    safe_bbox = (
        max(0, x0),
        max(0, y0),
        min(base.width, x0 + product_rgba.width),
        min(base.height, y0 + product_rgba.height),
    )
    return {
        "composite": composite,
        "product_alpha": alpha_canvas,
        "product_rgba": product_rgba,
        "bbox": safe_bbox,
    }


def _build_pose_hold_masks(*, canvas_size, product_alpha, bbox, wrists, hold_mode: str):
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    width, height = canvas_size
    x0, y0, x1, y1 = bbox
    box_w = max(1, x1 - x0)
    box_h = max(1, y1 - y0)
    if hold_mode == "Overhead Trophy":
        base_expand = max(8, int(min(box_w, box_h) * 0.075))
        erode = max(2, int(min(box_w, box_h) * 0.02))
    else:
        base_expand = max(10, int(min(box_w, box_h) * 0.1))
        erode = max(4, int(min(box_w, box_h) * 0.035))

    dilated = product_alpha.filter(ImageFilter.MaxFilter(size=base_expand * 2 + 1))
    eroded = product_alpha.filter(ImageFilter.MinFilter(size=max(3, erode * 2 + 1)))
    ring = ImageChops.subtract(dilated, eroded)

    contact = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(contact)

    for wrist in (wrists.get("left_wrist"), wrists.get("right_wrist")):
        if wrist is None:
            continue
        wx, wy = wrist
        rx = max(18, int(box_w * 0.16))
        ry = max(18, int(box_h * (0.12 if hold_mode == "Overhead Trophy" else 0.16)))
        draw.ellipse((wx - rx, wy - ry, wx + rx, wy + ry), fill=255)

    inpaint_mask = ImageChops.lighter(ring, contact)
    if hold_mode != "Overhead Trophy":
        extra = Image.new("L", (width, height), 0)
        extra_draw = ImageDraw.Draw(extra)
        extra_draw.rounded_rectangle(
            (
                max(0, x0 - base_expand),
                max(0, y0 - base_expand),
                min(width, x1 + base_expand),
                min(height, y1 + base_expand),
            ),
            radius=max(12, base_expand * 2),
            fill=160,
        )
        inpaint_mask = ImageChops.lighter(inpaint_mask, extra)
    inpaint_mask = inpaint_mask.filter(ImageFilter.GaussianBlur(radius=max(5, base_expand)))

    if hold_mode == "Overhead Trophy":
        preserve_mask = product_alpha.filter(ImageFilter.GaussianBlur(radius=max(1, erode // 2)))
    else:
        preserve_mask = product_alpha.filter(ImageFilter.MinFilter(size=max(3, erode * 2 + 1)))
        preserve_mask = preserve_mask.filter(ImageFilter.GaussianBlur(radius=max(2, erode // 2)))
    return inpaint_mask, preserve_mask


def _generate_pose_hold_product(
    identity_person_img,
    product_img,
    pose_reference_img=None,
    *,
    pose_state: dict[str, list[float]] | None = None,
    hold_mode: str,
    product_description: str,
    custom_prompt: str,
    x_offset: float,
    y_offset: float,
    scale_multiplier: float,
    auto_remove_bg: bool,
    bg_threshold: int,
    edge_softness: int,
    preserve_product_detail: bool,
    steps: int,
    guidance: float,
    seed: int,
):
    prepared = _prepare_pose_hold_product_generation(
        identity_person_img,
        product_img,
        pose_reference_img,
        pose_state=pose_state,
        hold_mode=hold_mode,
        product_description=product_description,
        custom_prompt=custom_prompt,
        x_offset=x_offset,
        y_offset=y_offset,
        scale_multiplier=scale_multiplier,
        auto_remove_bg=auto_remove_bg,
        bg_threshold=bg_threshold,
        edge_softness=edge_softness,
    )
    return _execute_pose_hold_product_generation(
        prepared,
        preserve_product_detail=preserve_product_detail,
        steps=steps,
        guidance=guidance,
        seed=seed,
    )


def _prepare_pose_hold_product_generation(
    identity_person_img,
    product_img,
    pose_reference_img=None,
    *,
    pose_state: dict[str, list[float]] | None = None,
    hold_mode: str,
    product_description: str,
    custom_prompt: str,
    x_offset: float,
    y_offset: float,
    scale_multiplier: float,
    auto_remove_bg: bool,
    bg_threshold: int,
    edge_softness: int,
):
    from PIL import Image

    identity_person = _to_pil_image(identity_person_img).convert("RGB")
    product = _to_pil_image(product_img)
    product_rgba = (
        _remove_product_background(product, threshold=bg_threshold, edge_softness=edge_softness)
        if auto_remove_bg
        else product.convert("RGBA")
    )
    product_aspect = product_rgba.height / max(product_rgba.width, 1)

    pose_points: dict[str, tuple[float, float] | None] = {}
    if pose_state:
        pose_reference = identity_person.copy()
        pose_map = _render_pose_map_from_state(pose_reference.size, pose_state)
        bbox, wrists = _estimate_product_bbox_from_pose_state(
            pose_state,
            pose_reference.size,
            product_aspect,
            hold_mode=hold_mode,
            x_offset=x_offset,
            y_offset=y_offset,
            scale_multiplier=scale_multiplier,
        )
        for joint_name in _POSE_EDITOR_JOINTS:
            pt = pose_state.get(joint_name)
            pose_points[joint_name] = (float(pt[0]), float(pt[1])) if pt else None
    else:
        pose_reference = _to_pil_image(pose_reference_img).convert("RGB")
        pose_map, pose_result = _extract_pose_map_and_result(pose_reference)
        bbox, wrists = _estimate_product_bbox_from_pose(
            pose_result,
            pose_reference.size,
            product_aspect,
            hold_mode=hold_mode,
            x_offset=x_offset,
            y_offset=y_offset,
            scale_multiplier=scale_multiplier,
        )
        body = pose_result.body.keypoints
        key_map = {
            "nose": 0,
            "neck": 1,
            "right_shoulder": 2,
            "right_elbow": 3,
            "right_wrist": 4,
            "left_shoulder": 5,
            "left_elbow": 6,
            "left_wrist": 7,
            "right_hip": 8,
            "left_hip": 11,
        }
        for joint_name, idx in key_map.items():
            pose_points[joint_name] = _hold_pose_keypoint_xy(body[idx] if len(body) > idx else None, pose_reference.size[0], pose_reference.size[1])

    if hold_mode == "Overhead Trophy" and wrists.get("left_wrist") is not None and wrists.get("right_wrist") is not None:
        prepared = _compose_product_between_wrists(
            pose_reference,
            product_rgba,
            wrists=wrists,
            pose_points=pose_points,
            x_offset=x_offset,
            y_offset=y_offset,
            scale_multiplier=scale_multiplier,
            auto_remove_bg=False,
            bg_threshold=bg_threshold,
            edge_softness=edge_softness,
        )
    else:
        prepared = _compose_product_at_bbox(
            pose_reference,
            product_rgba,
            bbox=bbox,
            auto_remove_bg=False,
            bg_threshold=bg_threshold,
            edge_softness=edge_softness,
        )
    init_image = prepared["composite"]
    inpaint_mask, preserve_mask = _build_pose_hold_masks(
        canvas_size=init_image.size,
        product_alpha=prepared["product_alpha"],
        bbox=prepared["bbox"],
        wrists=wrists,
        hold_mode=hold_mode,
    )

    if custom_prompt and custom_prompt.strip():
        prompt = custom_prompt.strip()
    elif hold_mode == "Overhead Trophy":
        prompt = (
            f"photorealistic person lifting {product_description.strip() or 'the uploaded product'} above their head with both hands, "
            "realistic grip on the handles, natural fingers around the handles, coherent anatomy, realistic shadows, face visible below the object, premium editorial sports celebration photo"
        )
    else:
        prompt = (
            f"photorealistic person holding {product_description.strip() or 'the uploaded product'} with both hands, "
            "realistic grip and finger placement, natural contact shadows, coherent anatomy, premium lifestyle photo"
        )

    negative_prompt = (
        "extra fingers, extra hands, deformed hands, broken wrists, floating object, duplicated object, "
        "warped face, child face, tiny body, bad anatomy, blurry, low quality, text, watermark, object covering face"
    )

    resized_init, original_size = _resize_for_sd(init_image)
    target_size = resized_init.size
    resized_mask = inpaint_mask.resize(target_size, Image.LANCZOS)
    resized_preserve = preserve_mask.resize(target_size, Image.LANCZOS)
    resized_pose_map = pose_map.resize(target_size, Image.LANCZOS)
    return {
        "identity_person": identity_person,
        "pose_map": pose_map,
        "inpaint_mask": inpaint_mask,
        "init_image": init_image,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "resized_init": resized_init,
        "resized_mask": resized_mask,
        "resized_preserve": resized_preserve,
        "resized_pose_map": resized_pose_map,
        "original_size": original_size,
        "prepared_product_bbox": prepared["bbox"],
    }


def _execute_pose_hold_product_generation(
    prepared: dict[str, Any],
    *,
    preserve_product_detail: bool,
    steps: int,
    guidance: float,
    seed: int,
):
    import numpy as np
    from PIL import Image

    def _run(pass_device: str):
        _, _, pipe = _ensure_hold_product_proper_pipeline(force_device=pass_device)
        generator_device = getattr(pipe, "_execution_device", pass_device)
        try:
            generator = torch.Generator(device=generator_device).manual_seed(int(seed))
        except Exception:
            generator = torch.Generator(device="cpu").manual_seed(int(seed))
        return pipe(
            prompt=prepared["prompt"],
            negative_prompt=prepared["negative_prompt"],
            image=prepared["resized_init"],
            mask_image=prepared["resized_mask"],
            control_image=prepared["resized_pose_map"],
            num_inference_steps=int(steps),
            guidance_scale=float(guidance),
            strength=0.9,
            generator=generator,
            controlnet_conditioning_scale=1.0,
        ).images[0]

    active_device = _preferred_device()
    result = _run(active_device)
    result_arr = np.asarray(result.convert("RGB"), dtype=np.float32)
    if active_device == "mps" and float(result_arr.mean()) < 2.0:
        global _HOLD_PRODUCT_PROPER_PIPE, _HOLD_PRODUCT_PROPER_DEVICE, _HOLD_PRODUCT_PROPER_ERROR
        _HOLD_PRODUCT_PROPER_PIPE = None
        _HOLD_PRODUCT_PROPER_DEVICE = None
        _HOLD_PRODUCT_PROPER_ERROR = None
        result = _run("cpu")

    if preserve_product_detail:
        result = Image.composite(prepared["resized_init"], result, prepared["resized_preserve"])

    if result.size != prepared["original_size"]:
        result = result.resize(prepared["original_size"], Image.LANCZOS)
    pose_map = prepared["pose_map"]
    inpaint_mask = prepared["inpaint_mask"]
    if pose_map.size != prepared["original_size"]:
        pose_map = pose_map.resize(prepared["original_size"], Image.LANCZOS)
    if inpaint_mask.size != prepared["original_size"]:
        inpaint_mask = inpaint_mask.resize(prepared["original_size"], Image.LANCZOS)

    backend_status = "pose control + local ControlNet SD15"
    face_status = "face preserved"
    try:
        result, face_mode = _run_insightface_face_swap(result, prepared["identity_person"], crop_scale=2.6)
        face_status = f"face restored ({face_mode})"
    except Exception as exc:
        face_status = f"face restore skipped: {exc}"

    return result, pose_map, inpaint_mask, f"{backend_status} | {face_status}"


def _default_pose_state(image_size: tuple[int, int], hold_mode: str):
    width, height = image_size

    def pt(xf: float, yf: float):
        return [float(width * xf), float(height * yf)]

    if hold_mode == "Overhead Trophy":
        return {
            "nose": pt(0.50, 0.20),
            "neck": pt(0.50, 0.30),
            "right_shoulder": pt(0.39, 0.34),
            "right_elbow": pt(0.28, 0.22),
            "right_wrist": pt(0.21, 0.10),
            "left_shoulder": pt(0.61, 0.34),
            "left_elbow": pt(0.72, 0.22),
            "left_wrist": pt(0.79, 0.10),
            "right_hip": pt(0.44, 0.56),
            "left_hip": pt(0.56, 0.56),
        }
    return {
        "nose": pt(0.50, 0.20),
        "neck": pt(0.50, 0.31),
        "right_shoulder": pt(0.40, 0.36),
        "right_elbow": pt(0.34, 0.43),
        "right_wrist": pt(0.31, 0.52),
        "left_shoulder": pt(0.60, 0.36),
        "left_elbow": pt(0.66, 0.43),
        "left_wrist": pt(0.69, 0.52),
        "right_hip": pt(0.44, 0.56),
        "left_hip": pt(0.56, 0.56),
    }


def _render_pose_map_from_state(image_size: tuple[int, int], pose_state: dict[str, list[float]]):
    from PIL import Image, ImageDraw

    width, height = image_size
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    edge_colors = {
        ("nose", "neck"): (255, 0, 0),
        ("neck", "right_shoulder"): (255, 170, 0),
        ("right_shoulder", "right_elbow"): (255, 220, 0),
        ("right_elbow", "right_wrist"): (170, 255, 0),
        ("neck", "left_shoulder"): (0, 255, 80),
        ("left_shoulder", "left_elbow"): (0, 255, 180),
        ("left_elbow", "left_wrist"): (0, 220, 255),
        ("neck", "right_hip"): (80, 120, 255),
        ("neck", "left_hip"): (180, 80, 255),
    }
    for a, b in _POSE_EDITOR_EDGES:
        pa = pose_state.get(a)
        pb = pose_state.get(b)
        if not pa or not pb:
            continue
        draw.line((pa[0], pa[1], pb[0], pb[1]), fill=edge_colors.get((a, b), (255, 255, 255)), width=max(4, width // 140))
    for joint_name in _POSE_EDITOR_JOINTS:
        pt = pose_state.get(joint_name)
        if not pt:
            continue
        r = max(5, width // 120)
        draw.ellipse((pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r), fill=(255, 255, 255))
    return canvas


def _render_pose_editor_preview(base_image, pose_state: dict[str, list[float]], selected_joint: str | None = None):
    from PIL import ImageDraw

    base = _to_pil_image(base_image).convert("RGB").copy()
    draw = ImageDraw.Draw(base, "RGBA")
    for a, b in _POSE_EDITOR_EDGES:
        pa = pose_state.get(a)
        pb = pose_state.get(b)
        if not pa or not pb:
            continue
        draw.line((pa[0], pa[1], pb[0], pb[1]), fill=(0, 255, 255, 220), width=max(4, base.width // 150))
    for joint_name in _POSE_EDITOR_JOINTS:
        pt = pose_state.get(joint_name)
        if not pt:
            continue
        r = max(6, base.width // 110)
        fill = (255, 80, 80, 240) if joint_name == selected_joint else (255, 255, 255, 220)
        draw.ellipse((pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r), fill=fill, outline=(0, 0, 0, 220), width=2)
        label = _POSE_EDITOR_LABELS.get(joint_name, joint_name)
        tx = pt[0] + r + 4
        ty = pt[1] - r - 2
        draw.rounded_rectangle((tx - 2, ty - 2, tx + len(label) * 7 + 4, ty + 16), radius=4, fill=(0, 0, 0, 140))
        draw.text((tx, ty), label, fill=(255, 255, 255, 230))
    return base


def _normalize_pose_state_json(pose_state_json, image_size: tuple[int, int], hold_mode: str):
    import json

    if isinstance(pose_state_json, dict) and pose_state_json:
        return pose_state_json
    if isinstance(pose_state_json, str) and pose_state_json.strip():
        try:
            parsed = json.loads(pose_state_json)
            if isinstance(parsed, dict) and parsed:
                return parsed
        except Exception:
            pass
    return _default_pose_state(image_size, hold_mode)


def _render_pose_editor_html(base_image, pose_state: dict[str, list[float]]):
    import base64
    import html
    import io
    import json

    image = _to_pil_image(base_image).convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    state_json = json.dumps(pose_state)
    labels_json = json.dumps(_POSE_EDITOR_LABELS)
    joints_json = json.dumps(_POSE_EDITOR_JOINTS)
    edges_json = json.dumps(_POSE_EDITOR_EDGES)
    width, height = image.size
    srcdoc = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<style>
html, body {{ margin:0; padding:0; background:#111; overflow:hidden; }}
#pose-editor-root {{ position:relative; width:100%; max-width:{width}px; aspect-ratio:{width}/{height}; background:#111; border-radius:10px; overflow:hidden; margin:0 auto; }}
#pose-editor-bg {{ width:100%; height:100%; object-fit:contain; display:block; }}
#pose-editor-svg {{ position:absolute; inset:0; width:100%; height:100%; touch-action:none; }}
</style>
</head>
<body>
<div id="pose-editor-root">
  <img id="pose-editor-bg" src="data:image/png;base64,{img_b64}" style="width:100%; height:100%; object-fit:contain; display:block;" />
  <svg id="pose-editor-svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" style="position:absolute; inset:0; width:100%; height:100%; touch-action:none;"></svg>
</div>
<script>
(() => {{
  const root = document.getElementById("pose-editor-root");
  if (!root || root.dataset.bound === "1") return;
  root.dataset.bound = "1";
  const svg = root.querySelector("#pose-editor-svg");
  const poseInput = window.parent.document.querySelector('#pose-state-json textarea, #pose-state-json input');
  if (!svg || !poseInput) return;
  const labels = {labels_json};
  const joints = {joints_json};
  const edges = {edges_json};
  let state = {state_json};
  let active = null;

  const syncInput = () => {{
    poseInput.value = JSON.stringify(state);
    poseInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
    poseInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
  }};

  const svgPoint = (evt) => {{
    const pt = svg.createSVGPoint();
    pt.x = evt.clientX;
    pt.y = evt.clientY;
    const ctm = svg.getScreenCTM();
    return ctm ? pt.matrixTransform(ctm.inverse()) : {{ x: 0, y: 0 }};
  }};

  const lineFor = (a, b) => {{
    const pa = state[a], pb = state[b];
    if (!pa || !pb) return '';
    return `<line x1="${{pa[0]}}" y1="${{pa[1]}}" x2="${{pb[0]}}" y2="${{pb[1]}}" stroke="#19e5f5" stroke-width="8" stroke-linecap="round" />`;
  }};

  const jointMarkup = (name) => {{
    const p = state[name];
    if (!p) return '';
    const label = labels[name] || name;
    return `
      <g data-joint="${{name}}" style="cursor:grab;">
        <circle cx="${{p[0]}}" cy="${{p[1]}}" r="10" fill="#fff" stroke="#111" stroke-width="3"></circle>
        <rect x="${{p[0] + 12}}" y="${{p[1] - 18}}" rx="4" ry="4" width="${{Math.max(52, label.length * 8)}}" height="20" fill="rgba(0,0,0,0.72)"></rect>
        <text x="${{p[0] + 18}}" y="${{p[1] - 4}}" fill="#fff" font-size="13" font-family="Arial, sans-serif">${{label}}</text>
      </g>
    `;
  }};

  const render = () => {{
    svg.innerHTML = edges.map(([a,b]) => lineFor(a,b)).join('') + joints.map(jointMarkup).join('');
    svg.querySelectorAll('[data-joint]').forEach((el) => {{
      el.addEventListener('pointerdown', (evt) => {{
        active = el.dataset.joint;
        el.style.cursor = 'grabbing';
        evt.preventDefault();
      }});
    }});
  }};

  const moveActive = (evt) => {{
    if (!active) return;
    const p = svgPoint(evt);
    state[active] = [Math.max(0, Math.min({width - 1}, p.x)), Math.max(0, Math.min({height - 1}, p.y))];
    render();
    syncInput();
    evt.preventDefault();
  }};

  svg.addEventListener('pointermove', moveActive);
  svg.addEventListener('pointerup', () => active = null);
  svg.addEventListener('pointerleave', () => active = null);
  svg.addEventListener('pointercancel', () => active = null);
  render();
  syncInput();
}})();
</script>
</body>
</html>
"""
    return f"""
<iframe
  title="Pose Editor"
  style="width:100%; max-width:{width}px; height:{height}px; border:0; border-radius:10px; background:#111;"
  srcdoc="{html.escape(srcdoc, quote=True)}"
></iframe>
"""


def _nearest_pose_joint(pose_state: dict[str, list[float]], x: float, y: float, max_radius: float = 36.0):
    nearest_name = None
    nearest_dist = max_radius
    for joint_name, pt in (pose_state or {}).items():
        if not pt:
            continue
        dx = float(pt[0]) - float(x)
        dy = float(pt[1]) - float(y)
        dist = (dx * dx + dy * dy) ** 0.5
        if dist <= nearest_dist:
            nearest_dist = dist
            nearest_name = joint_name
    return nearest_name


def _estimate_product_bbox_from_pose_state(
    pose_state: dict[str, list[float]],
    image_size: tuple[int, int],
    product_aspect: float,
    *,
    hold_mode: str,
    x_offset: float,
    y_offset: float,
    scale_multiplier: float,
):
    width, height = image_size
    right_wrist = pose_state.get("right_wrist")
    left_wrist = pose_state.get("left_wrist")
    right_shoulder = pose_state.get("right_shoulder")
    left_shoulder = pose_state.get("left_shoulder")

    hand_points = [pt for pt in (left_wrist, right_wrist) if pt is not None]
    shoulder_points = [pt for pt in (left_shoulder, right_shoulder) if pt is not None]
    if not hand_points:
        raise ValueError("Pose editor is missing wrist positions.")

    if len(hand_points) == 2:
        hands_mid_x = (hand_points[0][0] + hand_points[1][0]) / 2.0
        hands_mid_y = (hand_points[0][1] + hand_points[1][1]) / 2.0
        wrist_distance = abs(hand_points[0][0] - hand_points[1][0])
    else:
        hands_mid_x, hands_mid_y = hand_points[0]
        wrist_distance = width * 0.16

    if shoulder_points:
        shoulder_span = abs(shoulder_points[0][0] - shoulder_points[-1][0]) if len(shoulder_points) == 2 else width * 0.18
        shoulder_y = sum(pt[1] for pt in shoulder_points) / len(shoulder_points)
    else:
        shoulder_span = width * 0.18
        shoulder_y = hands_mid_y + height * 0.15

    base_width = max(shoulder_span * 0.55, wrist_distance * (0.95 if hold_mode == "Overhead Trophy" else 0.8), width * 0.12)
    target_w = max(56, int(base_width * float(scale_multiplier)))
    target_h = max(72, int(target_w * product_aspect))
    if hold_mode == "Overhead Trophy":
        center_x = hands_mid_x
        center_y = min(pt[1] for pt in hand_points) + target_h * 0.32
    else:
        center_x = hands_mid_x
        center_y = shoulder_y + target_h * 0.75
    center_x += width * float(x_offset)
    center_y += height * float(y_offset)
    x0 = int(round(center_x - target_w / 2))
    y0 = int(round(center_y - target_h / 2))
    x1 = x0 + target_w
    y1 = y0 + target_h
    return (x0, y0, x1, y1), {"left_wrist": left_wrist, "right_wrist": right_wrist}


def _ensure_insightface_swapper(*, force_cpu: bool = False):
    global _INSIGHTFACE_ANALYZER, _INSIGHTFACE_SWAPPER, _INSIGHTFACE_ERROR
    if force_cpu:
        _INSIGHTFACE_ANALYZER = None
        _INSIGHTFACE_SWAPPER = None
        _INSIGHTFACE_ERROR = None
    if _INSIGHTFACE_ANALYZER is not None and _INSIGHTFACE_SWAPPER is not None:
        return _INSIGHTFACE_ANALYZER, _INSIGHTFACE_SWAPPER
    if _INSIGHTFACE_ERROR:
        raise RuntimeError(_INSIGHTFACE_ERROR)

    try:
        from insightface.app import FaceAnalysis
        from insightface.model_zoo import get_model

        providers = _insightface_providers(prefer_cpu=force_cpu)
        root = str(_INSIGHTFACE_ROOT)
        analyzer = FaceAnalysis(
            name="antelopev2",
            root=root,
            providers=providers,
        )
        analyzer.prepare(ctx_id=0, det_thresh=0.45, det_size=(1024, 1024))
        swapper = get_model(
            _INSWAPPER_MODEL_NAME,
            root=root,
            providers=providers,
            download=True,
        )
        _INSIGHTFACE_ANALYZER = analyzer
        _INSIGHTFACE_SWAPPER = swapper
        _INSIGHTFACE_ERROR = None
        return analyzer, swapper
    except Exception as exc:
        _INSIGHTFACE_ERROR = str(exc)
        raise RuntimeError(str(exc)) from exc


def _run_insightface_face_swap(target_pil, source_pil, *, crop_scale: float = 3.0, force_cpu: bool = False):
    import numpy as np
    import cv2
    from PIL import Image

    analyzer, swapper = _ensure_insightface_swapper(force_cpu=force_cpu)

    target_bgr = cv2.cvtColor(np.array(target_pil.convert("RGB")), cv2.COLOR_RGB2BGR)
    source_bgr = cv2.cvtColor(np.array(source_pil.convert("RGB")), cv2.COLOR_RGB2BGR)

    try:
        target_faces = analyzer.get(target_bgr, max_num=0)
        source_faces = analyzer.get(source_bgr, max_num=0)
    except Exception as exc:
        if not force_cpu and "CoreML" in str(exc):
            return _run_insightface_face_swap(target_pil, source_pil, crop_scale=crop_scale, force_cpu=True)
        raise
    target_face = _select_primary_face(target_faces)
    source_face = _select_primary_face(source_faces)

    if target_face is None:
        raise ValueError("No target face detected.")
    if source_face is None:
        raise ValueError("No source face detected.")

    target_ratio = _bbox_area(target_face.bbox) / float(target_bgr.shape[0] * target_bgr.shape[1])
    mode = "full-frame"
    if target_ratio < 0.06:
        crop_box = _expand_bbox(target_face.bbox, target_bgr.shape, scale=crop_scale, shift_y=-0.2)
        if crop_box is not None:
            x0, y0, x1, y1 = crop_box
            target_crop = target_bgr[y0:y1, x0:x1].copy()
            crop_faces = analyzer.get(target_crop, max_num=0)
            crop_target_face = _select_primary_face(crop_faces)
            if crop_target_face is not None:
                swapped_crop = swapper.get(target_crop, crop_target_face, source_face, paste_back=True)
                swapped_crop = _refine_swapped_face(swapped_crop, target_crop, crop_target_face)
                swapped_bgr = target_bgr.copy()
                swapped_bgr[y0:y1, x0:x1] = swapped_crop
                mode = "face-crop"
            else:
                swapped_bgr = swapper.get(target_bgr, target_face, source_face, paste_back=True)
                swapped_bgr = _refine_swapped_face(swapped_bgr, target_bgr, target_face)
        else:
            swapped_bgr = swapper.get(target_bgr, target_face, source_face, paste_back=True)
            swapped_bgr = _refine_swapped_face(swapped_bgr, target_bgr, target_face)
    else:
        swapped_bgr = swapper.get(target_bgr, target_face, source_face, paste_back=True)
        swapped_bgr = _refine_swapped_face(swapped_bgr, target_bgr, target_face)

    swapped_rgb = cv2.cvtColor(swapped_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(swapped_rgb), mode


def _build_identity_masks(mask_result, *, include_hair: bool = True):
    import numpy as np
    from PIL import Image

    schp_lip = np.array(mask_result["schp_lip"])
    schp_atr = np.array(mask_result["schp_atr"])

    lip_face_map = [13]
    atr_face_map = [11]
    lip_head_map = [1, 2, 4, 13]
    atr_head_map = [1, 2, 3, 11]

    face_mask_np = np.zeros_like(schp_lip, dtype=bool)
    head_mask_np = np.zeros_like(schp_lip, dtype=bool)

    for idx in lip_face_map:
        face_mask_np |= (schp_lip == idx)
    for idx in atr_face_map:
        face_mask_np |= (schp_atr == idx)
    for idx in lip_head_map:
        head_mask_np |= (schp_lip == idx)
    for idx in atr_head_map:
        head_mask_np |= (schp_atr == idx)

    selected_mask = head_mask_np if include_hair else face_mask_np
    return {
        "face": Image.fromarray((face_mask_np * 255).astype(np.uint8)).convert("L"),
        "head": Image.fromarray((head_mask_np * 255).astype(np.uint8)).convert("L"),
        "selected": Image.fromarray((selected_mask * 255).astype(np.uint8)).convert("L"),
    }


def _mask_bbox(mask_img):
    import numpy as np

    mask_np = np.array(mask_img.convert("L")) > 0
    coords = np.argwhere(mask_np)
    if coords.size == 0:
        return None
    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0) + 1
    return (int(x0), int(y0), int(x1), int(y1))


def _estimate_head_bbox_from_body_mask(body_mask_img):
    body_bbox = _mask_bbox(body_mask_img)
    if body_bbox is None:
        return None

    x0, y0, x1, y1 = body_bbox
    body_w = x1 - x0
    body_h = y1 - y0
    if body_w <= 0 or body_h <= 0:
        return None

    head_w = max(1, int(body_w * 0.42))
    head_h = max(1, int(body_h * 0.24))
    head_x0 = x0 + max(0, (body_w - head_w) // 2)
    head_y0 = y0 + max(0, int(body_h * 0.02))
    return (head_x0, head_y0, head_x0 + head_w, head_y0 + head_h)


def _select_target_face_bbox(target_mask, fallback_body_mask=None):
    target_bbox = _mask_bbox(target_mask)
    fallback_bbox = _estimate_head_bbox_from_body_mask(fallback_body_mask) if fallback_body_mask is not None else None
    if target_bbox is None:
        return fallback_bbox

    if fallback_bbox is None:
        return target_bbox

    tx0, ty0, tx1, ty1 = target_bbox
    fx0, fy0, fx1, fy1 = fallback_bbox
    target_w = tx1 - tx0
    target_h = ty1 - ty0
    fallback_w = fx1 - fx0
    fallback_h = fy1 - fy0

    # When parsing fails on mannequins/statues, the "head" mask can cover
    # most of the torso. Prefer the silhouette-derived head zone in that case.
    if target_w > fallback_w * 1.6 or target_h > fallback_h * 1.6:
        return fallback_bbox
    if ty0 > fy1:
        return fallback_bbox
    return target_bbox


def _apply_face_swap(
    target_img,
    target_mask,
    source_img,
    source_mask,
    *,
    blend_strength: float,
    fallback_body_mask=None,
):
    from PIL import Image, ImageFilter

    source_bbox = _mask_bbox(source_mask)
    target_bbox = _select_target_face_bbox(target_mask, fallback_body_mask=fallback_body_mask)
    if source_bbox is None or target_bbox is None:
        return target_img

    sx0, sy0, sx1, sy1 = source_bbox
    tx0, ty0, tx1, ty1 = target_bbox
    source_crop = source_img.crop((sx0, sy0, sx1, sy1))
    source_alpha = source_mask.crop((sx0, sy0, sx1, sy1))
    if source_crop.size[0] <= 0 or source_crop.size[1] <= 0:
        return target_img

    target_w = max(1, tx1 - tx0)
    target_h = max(1, ty1 - ty0)
    resized_crop = source_crop.resize((target_w, target_h), Image.LANCZOS)
    resized_alpha = source_alpha.resize((target_w, target_h), Image.LANCZOS)

    if blend_strength < 1.0:
        resized_alpha = resized_alpha.point(lambda px: int(px * blend_strength))

    feather_radius = max(2, min(target_w, target_h) // 18)
    resized_alpha = resized_alpha.filter(ImageFilter.GaussianBlur(radius=feather_radius))

    overlay = Image.new("RGB", target_img.size)
    overlay.paste(resized_crop, (tx0, ty0))
    alpha_canvas = Image.new("L", target_img.size)
    alpha_canvas.paste(resized_alpha, (tx0, ty0))
    return Image.composite(overlay, target_img, alpha_canvas)


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
        # Force the CatVTON pipeline to use the shared-vault VAE path instead
        # of falling back to any bundled or remote default.
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
        print(f"[try-on] \u2713 Ready | Backend: {pipe_device.upper()}")
        
    except Exception as exc:
        import traceback
        _ERROR = f"{exc}\n{traceback.format_exc()}"
        _READY.set()
        print(f"[try-on] Load failed: {exc}")

def _inference(person_img, cloth_img, category, sleeve_length, pant_length, resolution, num_steps, guidance, seed, show_mask, mask_sharpness, mask_padding, detail_boost, face_restore_strength, preserve_head, face_swap_source_img, enable_face_swap, face_swap_include_hair, face_swap_strength, lock_seed, use_vae_hf, sampler_name, bg_plate, composite_strength, enable_deep_texture, warp_strength, progress=gr.Progress()):
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
    face_swap_only = bool(enable_face_swap and cloth_img is None)

    if person_img is None:
        yield None, None, "Please upload a person/body image.", gr.update(), gr.update()
        return
    if not face_swap_only and cloth_img is None:
        yield None, None, "Please upload a garment image, or enable face swap only.", gr.update(), gr.update()
        return
    if enable_face_swap and face_swap_source_img is None:
        yield None, None, "Please upload a face source image or disable face swap.", gr.update(), gr.update()
        return
    if face_swap_only and not enable_face_swap:
        yield None, None, "Enable face swap to use face-swap-only mode.", gr.update(), gr.update()
        return

    # 💾 Save Last Settings
    try:
        settings = {
            "category": category, "sleeve_length": sleeve_length, "pant_length": pant_length,
            "resolution": resolution, "steps": num_steps, "guidance": guidance,
            "seed": seed, "show_mask": show_mask, "mask_sharpness": mask_sharpness, "mask_padding": mask_padding,
            "detail_boost": detail_boost, "face_restore_strength": face_restore_strength, "preserve_head": preserve_head,
            "enable_face_swap": enable_face_swap, "face_swap_only": face_swap_only,
            "face_swap_include_hair": face_swap_include_hair, "face_swap_strength": face_swap_strength,
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
    if face_swap_source_img is not None and not isinstance(face_swap_source_img, Image.Image):
        face_swap_source_img = Image.fromarray(face_swap_source_img)

    # Standalone build uses the stable high-quality render path only.
    target_size = (768, 1024)
    person = resize_and_crop(person_img.convert("RGB"), target_size)
    cloth = resize_and_padding(cloth_img.convert("RGB"), target_size) if cloth_img is not None else None
    face_swap_source = None
    if face_swap_source_img is not None:
        face_swap_source = resize_and_crop(face_swap_source_img.convert("RGB"), target_size)
    
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
    
    # Build the optional head mask used for source-image head recomposition.
    head_mask_pil = None
    if preserve_head:
        identity_masks = _build_identity_masks(mask_result, include_hair=True)
        head_mask_pil = identity_masks["head"]
    
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
    
    result_img = None
    t_diff = 0.0
    if face_swap_only:
        progress(0.2, desc=f"Masking done ({t_mask:.1f}s). Building face-swap-only result...")
        result_img = person.copy()
    else:
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
    
    # Restore higher-frequency garment texture details from the source image.
    if enable_deep_texture and cloth_img is not None:
        progress(0.91, desc="Warping Original Textures...")
        from warp_repair import texture_repair_pass
        result_img = texture_repair_pass(cloth_img, result_img, mask_pil, warp_strength=warp_strength)
    
    # Re-composite the preserved head region from the source person image.
    if preserve_head and head_mask_pil is not None:
        progress(0.92, desc="Recompositing preserved head region...")
        head_src = person.resize(result_img.size, Image.LANCZOS) if person.size != result_img.size else person
        head_alpha = head_mask_pil.resize(result_img.size, Image.LANCZOS) if head_mask_pil.size != result_img.size else head_mask_pil
        feathered_head = head_alpha.filter(ImageFilter.GaussianBlur(radius=3))
        result_img = Image.composite(head_src, result_img, feathered_head)

    if enable_face_swap and face_swap_source is not None:
        progress(0.94, desc="Swapping face onto target body...")
        target_masks = _build_identity_masks(mask_result, include_hair=face_swap_include_hair)
        source_mask_result = _MASKER(face_swap_source, "upper", sleeve_length="default", pant_length="default")
        source_masks = _build_identity_masks(source_mask_result, include_hair=face_swap_include_hair)
        result_img = _apply_face_swap(
            result_img,
            target_masks["selected"],
            face_swap_source,
            source_masks["selected"],
            blend_strength=float(face_swap_strength),
            fallback_body_mask=full_body_mask_pil,
        )

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
    mode_label = "Face Swap" if face_swap_only else "Try-On"
    yield result_img, mask_out, f"✓ {mode_label} Ready | Latency: {t_mask+t_diff:.1f}s", gr.update(), gr.update(interactive=True, value="Generate Try-On")

# ── Gradio UI ─────────────────────────────────────────────────────────────────
def load_settings():
    migrate_legacy_settings(app_root=_ROOT, models_root=_MODELS_ROOT)
    return load_saved_settings(app_root=_ROOT, models_root=_MODELS_ROOT)

def build_ui():
    s = load_settings()

    with gr.Blocks(title="Try-On Local") as demo:
        gr.HTML(get_navbar("try-on"))
        gr.Markdown("# Lightweight Local Virtual Try-On")
        
        with gr.Row():
            with gr.Column():
                person_in = gr.Image(label="Person Photo", type="numpy")
                cloth_in  = gr.Image(label="Garment Image", type="numpy")
                face_swap_source_in = gr.Image(label="Face Source (Optional)", type="numpy")
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
                    enable_face_swap = gr.Checkbox(label="Enable Face Swap", value=s.get("enable_face_swap", False))
                    face_swap_only = gr.Checkbox(label="Face Swap Only (No Garment Try-On)", value=s.get("face_swap_only", False))
                    face_swap_include_hair = gr.Checkbox(label="Include Hair / Hat / Glasses In Swap", value=s.get("face_swap_include_hair", False))
                    face_swap_strength = gr.Slider(0.1, 1.0, value=s.get("face_swap_strength", 1.0), step=0.1, label="Face Swap Blend")
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

        def apply_face_swap_only_mode(checked):
            if checked:
                return gr.update(value=None), gr.update(value=True)
            return gr.update(), gr.update()

        face_swap_only.change(
            fn=apply_face_swap_only_mode,
            inputs=[face_swap_only],
            outputs=[cloth_in, enable_face_swap],
        )

        show_mask.change(lambda v: gr.update(visible=v), show_mask, mask_out)
        run_btn.click(
            fn=_inference,
            inputs=[
                person_in, cloth_in, category, sleeve_length, pant_length, resolution, steps, guidance, seed, 
                show_mask, mask_sharpness, mask_padding, detail_boost, face_restore_strength, preserve_head,
                face_swap_source_in, enable_face_swap, face_swap_include_hair, face_swap_strength, lock_seed, use_vae_hf,
                sampler, bg_plate, composite_strength, enable_deep_texture, warp_strength
            ],
            outputs=[result_out, mask_out, status_out, seed, run_btn],
            show_progress="hidden"
        )

    return demo


def build_face_swap_ui():
    s = load_settings()

    def run_standalone_face_swap(person_img, face_swap_source_img, sports_portrait_mode, face_swap_include_hair, face_swap_strength, show_mask, seed_value):
        if person_img is None:
            yield None, None, "Please upload a target body/person image.", gr.update(), gr.update(interactive=True, value="Generate Face Swap")
            return
        if face_swap_source_img is None:
            yield None, None, "Please upload a face source image.", gr.update(), gr.update(interactive=True, value="Generate Face Swap")
            return

        from PIL import Image

        target_pil = person_img if isinstance(person_img, Image.Image) else Image.fromarray(person_img)
        source_pil = face_swap_source_img if isinstance(face_swap_source_img, Image.Image) else Image.fromarray(face_swap_source_img)

        yield None, None, "🚀 Launching...", gr.update(), gr.update(interactive=False, value="⌛ Generating...")

        try:
            crop_scale = 3.8 if sports_portrait_mode else 3.0
            result_img, swap_mode = _run_insightface_face_swap(target_pil, source_pil, crop_scale=crop_scale)
            mode_suffix = "sports-portrait" if sports_portrait_mode else "standard"
            yield result_img, None, f"✓ Face Swap Ready | Backend: InsightFace InSwapper (CoreML/CPU, {swap_mode}, {mode_suffix})", gr.update(), gr.update(interactive=True, value="Generate Face Swap")
            return
        except ValueError as exc:
            if "target face" not in str(exc).lower():
                yield None, None, f"❌ {exc}", gr.update(), gr.update(interactive=True, value="Generate Face Swap")
                return
            yield None, None, "⚠️ No target face detected for InsightFace. Falling back to geometric composite...", gr.update(), gr.update(interactive=False, value="⌛ Fallback...")
        except Exception as exc:
            detail = str(exc)
            yield None, None, f"⚠️ InsightFace failed: {detail}. Falling back to geometric composite...", gr.update(), gr.update(interactive=False, value="⌛ Fallback...")

        # Fallback when the target has no detectable facial landmarks.
        for result_img, mask_img, status_text, _, button_state in _inference(
            target_pil,
            None,
            "Upper (T-Shirts, Hoodies)",
            "default",
            "default",
            "High Quality",
            20,
            3.5,
            seed_value,
            show_mask,
            12,
            6,
            0.0,
            0.0,
            False,
            source_pil,
            True,
            face_swap_include_hair,
            face_swap_strength,
            True,
            True,
            "Euler A",
            None,
            0.0,
            False,
            1.0,
        ):
            if status_text and status_text.startswith("✓"):
                status_text = f"{status_text} | Backend: fallback geometric composite"
            yield result_img, mask_img, status_text, gr.update(), button_state

    with gr.Blocks(title="Face Swap Local") as demo:
        gr.HTML(get_navbar("face-swap"))
        gr.Markdown("# Standalone Face Swap")

        with gr.Row():
            with gr.Column():
                person_in = gr.Image(label="Target Body / Person", type="numpy")
                face_swap_source_in = gr.Image(label="Face Source", type="numpy")
            with gr.Column():
                sports_portrait_mode = gr.Checkbox(
                    label="Sports Portrait Swap Mode",
                    value=True,
                )
                face_swap_include_hair = gr.Checkbox(
                    label="Include Hair / Hat / Glasses In Swap",
                    value=False,
                )
                face_swap_strength = gr.Slider(
                    0.1, 1.0, value=s.get("face_swap_strength", 1.0), step=0.1, label="Face Swap Blend"
                )
                show_mask = gr.Checkbox(label="Show Masking Step (Debug)", value=s.get("show_mask", False))
                seed_dummy = gr.Number(value=s.get("seed", 42), precision=0, visible=False)
                run_btn = gr.Button("Generate Face Swap", variant="primary")
                status_out = gr.Textbox(label="Status", interactive=False, container=False)
                result_out = gr.Image(label="Result", interactive=False)
                mask_out = gr.Image(label="Mask", visible=False)

        show_mask.change(lambda v: gr.update(visible=v), show_mask, mask_out)
        run_btn.click(
            fn=run_standalone_face_swap,
            inputs=[
                person_in,
                face_swap_source_in,
                sports_portrait_mode,
                face_swap_include_hair,
                face_swap_strength,
                show_mask,
                seed_dummy,
            ],
            outputs=[result_out, mask_out, status_out, seed_dummy, run_btn],
            show_progress="hidden",
        )

    return demo


def build_hold_product_ui():
    def resolve_pose_reference(pose_reference_img):
        if pose_reference_img is not None:
            return pose_reference_img
        return None

    def init_pose_editor(person_img, hold_mode, pose_template_name):
        if person_img is None:
            return "<div style='padding:16px;color:#bbb'>Upload a person image to start posing.</div>", "{}"
        base_img = _to_pil_image(person_img).convert("RGB")
        template_mode = _HOLD_PRODUCT_TEMPLATE_MODES.get(str(pose_template_name or ""), str(hold_mode))
        pose_state = _default_pose_state(base_img.size, str(template_mode))
        return _render_pose_editor_html(base_img, pose_state), json.dumps(pose_state)

    def run_hold_product(
        person_img,
        product_img,
        pose_reference_img,
        pose_template_name,
        pose_state_json,
        hold_mode,
        product_description,
        custom_prompt,
        x_offset,
        y_offset,
        scale_multiplier,
        auto_remove_bg,
        bg_threshold,
        edge_softness,
        preserve_product_detail,
        steps,
        guidance,
        show_pose_map,
        show_mask,
        seed,
        progress=gr.Progress(track_tqdm=False),
    ):
        if person_img is None:
            yield None, None, None, "Please upload a person identity image.", gr.update()
            return
        if product_img is None:
            yield None, None, None, "Please upload a product PNG image.", gr.update()
            return
        base_person = _to_pil_image(person_img).convert("RGB")
        pose_reference_img = resolve_pose_reference(pose_reference_img)
        pose_state = _normalize_pose_state_json(pose_state_json, base_person.size, str(hold_mode))
        if pose_reference_img is None and not pose_state:
            yield None, None, None, "Please define a pose in the editor.", gr.update()
            return

        try:
            progress(0.02, desc="Preparing pose-controlled generation")
            yield None, None, None, "⌛ Building pose-controlled generation...", gr.update(interactive=False, value="⌛ Generating...")
            prepared = _prepare_pose_hold_product_generation(
                person_img,
                product_img,
                pose_reference_img,
                pose_state=dict(pose_state or {}),
                hold_mode=str(hold_mode),
                product_description=str(product_description or ""),
                custom_prompt=str(custom_prompt or ""),
                x_offset=float(x_offset),
                y_offset=float(y_offset),
                scale_multiplier=float(scale_multiplier),
                auto_remove_bg=bool(auto_remove_bg),
                bg_threshold=int(bg_threshold),
                edge_softness=int(edge_softness),
            )
            progress(0.18, desc="Pose map and product placement prepared")
            yield (
                prepared["init_image"],
                prepared["pose_map"] if show_pose_map else None,
                prepared["inpaint_mask"] if show_mask else None,
                "⌛ Pose map ready. Product placement preview built.",
                gr.update(interactive=False, value="⌛ Generating..."),
            )
            progress(0.32, desc="Inpaint mask built")
            yield (
                prepared["init_image"],
                prepared["pose_map"] if show_pose_map else None,
                prepared["inpaint_mask"] if show_mask else None,
                "⌛ Mask built. Running diffusion pass...",
                gr.update(interactive=False, value="⌛ Generating..."),
            )
            progress(0.45, desc="Running diffusion")
            result, pose_map, debug_mask, backend_status = _execute_pose_hold_product_generation(
                prepared,
                preserve_product_detail=bool(preserve_product_detail),
                steps=int(steps),
                guidance=float(guidance),
                seed=int(seed),
            )
            progress(0.92, desc="Finalizing result")
            yield (
                result,
                pose_map if show_pose_map else None,
                debug_mask if show_mask else None,
                f"✓ Hold Product Ready | Backend: {backend_status}",
                gr.update(interactive=True, value="Generate Hold Product"),
            )
            progress(1.0, desc="Done")
        except Exception as exc:
            yield None, None, None, f"❌ Hold Product failed: {exc}", gr.update(interactive=True, value="Generate Hold Product")

    with gr.Blocks(title="Hold Product Local") as demo:
        gr.HTML(get_navbar("hold-product"))
        gr.Markdown("# Hold Product")
        gr.Markdown("Upload the source person for face identity and the transparent PNG product, then define the pose directly on the person with the skeleton editor. A pose-image override is still available when you want to drive the generation from an external reference.")

        with gr.Row():
            with gr.Column():
                person_in = gr.Image(label="Person Identity Image", type="numpy")
                product_in = gr.Image(label="Product PNG", type="numpy")
                gr.Markdown("Drag the joints directly on the pose canvas. The rig is an upper-body OpenPose control rig, not a medical skeleton.")
                pose_editor_html = gr.HTML(label="Pose Editor")
                pose_state_json = gr.Textbox(value="{}", visible=True, container=False, elem_id="pose-state-json", elem_classes=["pose-state-hidden"])
                with gr.Accordion("Pose Reference Override (Optional)", open=False):
                    pose_reference_in = gr.Image(label="Pose Reference Image", type="numpy")
            with gr.Column():
                pose_template = gr.Dropdown(
                    choices=list(_HOLD_PRODUCT_POSE_TEMPLATES.keys()),
                    value="Trophy Overhead",
                    label="Built-In Pose Template",
                )
                hold_mode = gr.Dropdown(
                    choices=["Overhead Trophy", "Front Hold"],
                    value="Overhead Trophy",
                    label="Hold Mode",
                )
                product_description = gr.Textbox(
                    label="Product Description",
                    value="silver trophy cup",
                    placeholder="silver trophy cup, perfume bottle, sneaker box...",
                )
                custom_prompt = gr.Textbox(
                    label="Custom Prompt Override (Optional)",
                    placeholder="photorealistic person lifting a silver trophy above their head with both hands",
                )
                x_offset = gr.Slider(-0.2, 0.2, value=0.0, step=0.01, label="Horizontal Placement Offset")
                y_offset = gr.Slider(-0.2, 0.2, value=0.0, step=0.01, label="Vertical Placement Offset")
                scale_multiplier = gr.Slider(0.6, 1.6, value=1.0, step=0.02, label="Product Scale Multiplier")
                auto_remove_bg = gr.Checkbox(label="Auto Remove Product Background", value=True)
                bg_threshold = gr.Slider(5, 120, value=28, step=1, label="Background Removal Threshold")
                edge_softness = gr.Slider(0, 20, value=4, step=1, label="Edge Softness")
                preserve_product_detail = gr.Checkbox(label="Preserve Core Product Detail", value=True)
                steps = gr.Slider(12, 40, value=26, step=1, label="Generative Steps")
                guidance = gr.Slider(3.0, 10.0, value=6.5, step=0.5, label="Guidance")
                seed = gr.Number(label="Seed", value=42, precision=0)
                show_pose_map = gr.Checkbox(label="Show Pose Map (Debug)", value=False)
                show_mask = gr.Checkbox(label="Show Inpaint Mask (Debug)", value=False)
                run_btn = gr.Button("Generate Hold Product", variant="primary")
                status_out = gr.Textbox(label="Status", interactive=False, container=False)
                result_out = gr.Image(label="Result", interactive=False)
                pose_map_out = gr.Image(label="Pose Map", interactive=False, visible=False)
                mask_out = gr.Image(label="Inpaint Mask", interactive=False, visible=False)
        person_in.change(
            fn=init_pose_editor,
            inputs=[person_in, hold_mode, pose_template],
            outputs=[pose_editor_html, pose_state_json],
            show_progress="hidden",
        )
        hold_mode.change(
            fn=init_pose_editor,
            inputs=[person_in, hold_mode, pose_template],
            outputs=[pose_editor_html, pose_state_json],
            show_progress="hidden",
        )
        pose_template.change(
            fn=init_pose_editor,
            inputs=[person_in, hold_mode, pose_template],
            outputs=[pose_editor_html, pose_state_json],
            show_progress="hidden",
        )
        show_pose_map.change(lambda v: gr.update(visible=v), show_pose_map, pose_map_out)
        show_mask.change(lambda v: gr.update(visible=v), show_mask, mask_out)
        run_btn.click(
            fn=run_hold_product,
            inputs=[
                person_in,
                product_in,
                pose_reference_in,
                pose_template,
                pose_state_json,
                hold_mode,
                product_description,
                custom_prompt,
                x_offset,
                y_offset,
                scale_multiplier,
                auto_remove_bg,
                bg_threshold,
                edge_softness,
                preserve_product_detail,
                steps,
                guidance,
                show_pose_map,
                show_mask,
                seed,
            ],
            outputs=[result_out, pose_map_out, mask_out, status_out, run_btn],
            show_progress="full",
        )

    return demo

from fastapi import FastAPI, HTTPException, Request, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import shutil
import json
from pydantic import BaseModel, Field

from image_to_video_page import (
    ImageToVideoApiRequest,
    build_image_to_video_ui,
    run_image_to_video_api,
)

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
    html = html.replace("{{ 'active' if active == 'face-swap' else '' }}", "active" if active == "face-swap" else "")
    html = html.replace("{{ 'active' if active == 'hold-product' else '' }}", "active" if active == "hold-product" else "")
    html = html.replace("{{ 'active' if active == 'image-to-video' else '' }}", "active" if active == "image-to-video" else "")
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
    garment_image_path: str | None = None
    output_image_path: str
    face_image_path: str | None = None
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
    enable_face_swap: bool = False
    face_swap_only: bool = False
    face_swap_include_hair: bool = False
    face_swap_strength: float = 1.0
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
    garment_path = Path(payload.garment_image_path).expanduser().resolve() if payload.garment_image_path else None
    output_path = Path(payload.output_image_path).expanduser().resolve()
    face_path = Path(payload.face_image_path).expanduser().resolve() if payload.face_image_path else None

    if not person_path.exists():
        raise HTTPException(status_code=400, detail=f"Person image not found: {person_path}")
    if not payload.face_swap_only and garment_path is None:
        raise HTTPException(status_code=400, detail="garment_image_path is required unless face_swap_only is true.")
    if garment_path is not None and not garment_path.exists():
        raise HTTPException(status_code=400, detail=f"Garment image not found: {garment_path}")
    if payload.enable_face_swap and face_path is None:
        raise HTTPException(status_code=400, detail="face_image_path is required when enable_face_swap is true.")
    if payload.face_swap_only and not payload.enable_face_swap:
        raise HTTPException(status_code=400, detail="enable_face_swap must be true when face_swap_only is true.")
    if face_path is not None and not face_path.exists():
        raise HTTPException(status_code=400, detail=f"Face image not found: {face_path}")

    person_img = Image.open(person_path).convert("RGB")
    cloth_img = Image.open(garment_path).convert("RGB") if garment_path else None
    face_img = Image.open(face_path).convert("RGB") if face_path else None

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
        face_img,
        payload.enable_face_swap,
        payload.face_swap_include_hair,
        payload.face_swap_strength,
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

    @fastapi_app.post("/api/image-to-video/run")
    async def run_image_to_video_api_route(payload: ImageToVideoApiRequest):
        try:
            return JSONResponse(run_image_to_video_api(payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    face_swap_source_img,
    enable_face_swap,
    face_swap_include_hair,
    face_swap_strength,
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
        face_swap_source_img,
        enable_face_swap,
        face_swap_include_hair,
        face_swap_strength,
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
    face_swap_demo = build_face_swap_ui()
    hold_product_demo = build_hold_product_ui()
    image_to_video_demo = build_image_to_video_ui(get_navbar)

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
    gradio_extra_css = "footer, .built-with-gradio, .pose-state-hidden { display: none !important; }"

    app = gr.mount_gradio_app(fastapi_app, demo, path="/try-on", theme=gradio_theme, css=gradio_extra_css)
    app = gr.mount_gradio_app(app, face_swap_demo, path="/face-swap", theme=gradio_theme, css=gradio_extra_css)
    app = gr.mount_gradio_app(app, hold_product_demo, path="/hold-product", theme=gradio_theme, css=gradio_extra_css)
    app = gr.mount_gradio_app(app, image_to_video_demo, path="/image-to-video", theme=gradio_theme, css=gradio_extra_css)

    uvicorn.run(app, host="127.0.0.1", port=7860)
