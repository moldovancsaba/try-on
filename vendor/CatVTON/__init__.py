from __future__ import annotations

import os
import sys

# catvton-d2-path — vendored Detectron2 + DensePose (register nodes without PYTHONPATH / make comfy).
def _bootstrap_catvton_sys_path() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    d2 = os.path.join(root, "model", "SCHP", "mhp_extension", "detectron2")
    dp = os.path.join(d2, "projects", "DensePose")
    if os.path.isdir(os.path.join(d2, "detectron2")) and os.path.isdir(os.path.join(dp, "densepose")):
        for p in (d2, dp):
            if p not in sys.path:
                sys.path.insert(0, p)


_bootstrap_catvton_sys_path()

import torch
import numpy as np
from PIL import Image
from diffusers.image_processor import VaeImageProcessor
from huggingface_hub import snapshot_download
from torchvision.transforms.functional import to_pil_image, to_tensor

from .utils import resize_and_crop, resize_and_padding


def _resolve_catvton_hub_dir(catvton_path: str) -> str:
    """
    If ``catvton_path`` is a local directory containing CatVTON's ``DensePose`` + ``SCHP`` trees, use it;
    otherwise treat it as a Hugging Face ``repo_id`` and ``snapshot_download``.
    """

    p = os.path.expanduser(catvton_path.strip())
    if os.path.isdir(p) and os.path.isdir(os.path.join(p, "DensePose")) and os.path.isdir(os.path.join(p, "SCHP")):
        return os.path.abspath(p)
    return snapshot_download(repo_id=catvton_path)

# catvton-apple-patch
def _catvton_device_str() -> str:
    import torch as _t
    if _t.cuda.is_available():
        return "cuda"
    if getattr(_t.backends, "mps", None) and _t.backends.mps.is_available():
        return "mps"
    return "cpu"


CATVTON_DEVICE_STR = _catvton_device_str()
CATVTON_RNG_DEVICE = "cpu" if CATVTON_DEVICE_STR != "cuda" else "cuda"


class LoadCatVTONPipeline:
    display_name = "Load CatVTON Pipeline"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "sd15_inpaint_path": ("STRING", {"default": "booksforcharlie/stable-diffusion-inpainting"}),
                "catvton_path": ("STRING", {"default": "zhengchong/CatVTON"}),
                "mixed_precision": (["fp32", "fp16", "bf16"],),
            }
        }

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("pipe",)
    FUNCTION = "load"
    CATEGORY = "CatVTON" 
        
    def load(self, sd15_inpaint_path, catvton_path, mixed_precision):
        from .model.pipeline import CatVTONPipeline

        mixed_precision = {
            "fp32": torch.float32,
            "fp16": torch.float16,
            "bf16": torch.bfloat16,
        }[mixed_precision]
        pipeline = CatVTONPipeline(
            base_ckpt=sd15_inpaint_path,
            attn_ckpt=catvton_path,
            attn_ckpt_version="mix",
            weight_dtype=mixed_precision,
            use_tf32=True,
            device=CATVTON_DEVICE_STR
        )
        return (pipeline,)


class LoadAutoMasker:
    display_name = "Load AutoMask Generator"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "catvton_path": ("STRING", {"default": "zhengchong/CatVTON"}),
            }
        }

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("pipe",)
    FUNCTION = "load"
    CATEGORY = "CatVTON" 
        
    def load(self, catvton_path):
        from .model.cloth_masker import AutoMasker as AM

        catvton_path = _resolve_catvton_hub_dir(catvton_path)
        automasker = AM(
            densepose_ckpt=os.path.join(catvton_path, "DensePose"),
            schp_ckpt=os.path.join(catvton_path, "SCHP"),
            device=CATVTON_DEVICE_STR,
        )
        return (automasker,)


class CatVTON:
    display_name = "TryOn by CatVTON"
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "pipe": ("MODEL",),
                "target_image": ("IMAGE",),
                "refer_image": ("IMAGE",),
                "mask_image": ("IMAGE",),
                "seed": ("INT", {"default": 42, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
                "steps": ("INT", {"default": 50, "min": 1, "max": 10000}),
                "cfg": (
                    "FLOAT",
                    {
                        "default": 2.5,
                        "min": 0.0,
                        "max": 14.0,
                        "step": 0.1,
                        "round": 0.01,
                    },
                ),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "generate"

    CATEGORY = "CatVTON" 

    def generate(self, pipe, target_image, refer_image, mask_image, seed, steps, cfg):
        target_image, refer_image, mask_image = [_.squeeze(0).permute(2, 0, 1) for _ in [target_image, refer_image, mask_image]]
        target_image = to_pil_image(target_image)
        refer_image = to_pil_image(refer_image)
        mask_image = mask_image[0]
        mask_image = to_pil_image(mask_image)
        generator = torch.Generator(device=CATVTON_RNG_DEVICE).manual_seed(seed)
        person_image = resize_and_crop(target_image, (768, 1024))
        cloth_image = resize_and_padding(refer_image, (768, 1024))
        mask = resize_and_crop(mask_image, (768, 1024))
        mask_processor = VaeImageProcessor(vae_scale_factor=8, do_normalize=False, do_binarize=True, do_convert_grayscale=True)
        mask = mask_processor.blur(mask, blur_factor=9)

        # Inference
        result_image = pipe(
            image=person_image,
            condition_image=cloth_image,
            mask=mask,
            num_inference_steps=steps,
            guidance_scale=cfg,
            generator=generator
        )[0]

        result_image = to_tensor(result_image).permute(1, 2, 0).unsqueeze(0)
        return (result_image,)


class AutoMasker:
    display_name = "Auto Mask Generation"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "pipe": ("MODEL",),
                "target_image": ("IMAGE",),
                "cloth_type": (["upper", "lower", 'overall'],),
            }
        }

    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("image", "image_masked")
    FUNCTION = "generate"

    CATEGORY = "CatVTON" 

    def generate(self, pipe, target_image, cloth_type):
        from .model.cloth_masker import vis_mask

        target_image = target_image.squeeze(0).permute(2, 0, 1)
        target_image = to_pil_image(target_image)
        person_image = resize_and_crop(target_image, (768, 1024))
        mask = pipe(
            person_image,
            cloth_type
        )['mask']
        
        masked_image = vis_mask(person_image, mask)
        mask = to_tensor(mask).permute(1, 2, 0).repeat(1, 1, 3).unsqueeze(0)
        masked_image = to_tensor(masked_image).permute(1, 2, 0).unsqueeze(0)

        return (mask, masked_image)


_export_classes = [
    LoadCatVTONPipeline,
    LoadAutoMasker,
    CatVTON,
    AutoMasker,
]

NODE_CLASS_MAPPINGS = {c.__name__: c for c in _export_classes}

NODE_DISPLAY_NAME_MAPPINGS = {
    c.__name__: getattr(c, "display_name", c.__name__) for c in _export_classes
}