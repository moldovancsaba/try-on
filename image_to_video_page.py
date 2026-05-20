from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

import gradio as gr
import torch
from huggingface_hub import snapshot_download
from model_paths import get_models_root
from pydantic import BaseModel


_ROOT = Path(__file__).resolve().parent
_MODELS_ROOT = get_models_root()
_VIDEO_OUTPUTS_DIR = _ROOT / "outputs" / "image_to_video"
_SVD_REPO_ID = "stabilityai/stable-video-diffusion-img2vid-xt"
_SVD_DIR = _MODELS_ROOT / "checkpoints" / "stable-video-diffusion-img2vid-xt"
_PIPE_LOCK = threading.Lock()
_PIPE = None
_PIPE_DEVICE = None
_PIPE_ERROR = None
_REQUIRED_MODEL_FILES = (
    "model_index.json",
    "feature_extractor/preprocessor_config.json",
    "scheduler/scheduler_config.json",
    "image_encoder/config.json",
    "image_encoder/model.safetensors",
    "image_encoder/model.fp16.safetensors",
    "unet/config.json",
    "unet/diffusion_pytorch_model.safetensors",
    "unet/diffusion_pytorch_model.fp16.safetensors",
    "vae/config.json",
    "vae/diffusion_pytorch_model.safetensors",
    "vae/diffusion_pytorch_model.fp16.safetensors",
)


class ImageToVideoApiRequest(BaseModel):
    source_image_path: str
    output_video_path: str
    num_frames: int = 14
    num_inference_steps: int = 20
    fps: int = 7
    motion_bucket_id: int = 140
    noise_aug_strength: float = 0.05
    min_guidance_scale: float = 1.0
    max_guidance_scale: float = 3.0
    seed: int = 42


def _preferred_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@contextmanager
def _online_hf_access():
    keys = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _model_is_present() -> bool:
    return all((_SVD_DIR / rel_path).exists() for rel_path in _REQUIRED_MODEL_FILES)


def _ensure_model_downloaded(progress: gr.Progress | None = None) -> Path:
    if _model_is_present():
        return _SVD_DIR

    if progress:
        progress(0.02, desc="Downloading Stable Video Diffusion weights...")

    _SVD_DIR.parent.mkdir(parents=True, exist_ok=True)
    if _SVD_DIR.exists():
        for stale_file in _SVD_DIR.rglob("*.incomplete"):
            stale_file.unlink(missing_ok=True)
        for stale_file in _SVD_DIR.rglob("*.lock"):
            stale_file.unlink(missing_ok=True)

    with _online_hf_access():
        snapshot_download(
            repo_id=_SVD_REPO_ID,
            local_dir=str(_SVD_DIR),
            local_dir_use_symlinks=False,
            allow_patterns=list(_REQUIRED_MODEL_FILES),
            force_download=False,
            max_workers=1,
        )

    if not _model_is_present():
        missing = [rel_path for rel_path in _REQUIRED_MODEL_FILES if not (_SVD_DIR / rel_path).exists()]
        raise RuntimeError(
            "Stable Video Diffusion download is incomplete. Missing files: "
            + ", ".join(missing)
        )
    return _SVD_DIR


def _load_pipeline(progress: gr.Progress | None = None):
    global _PIPE, _PIPE_DEVICE, _PIPE_ERROR

    if _PIPE is not None:
        return _PIPE

    with _PIPE_LOCK:
        if _PIPE is not None:
            return _PIPE

        try:
            from diffusers import StableVideoDiffusionPipeline

            model_dir = _ensure_model_downloaded(progress=progress)
            device = _preferred_device()
            dtype = torch.float16 if device in {"cuda", "mps"} else torch.float32

            if progress:
                progress(0.08, desc="Loading Stable Video Diffusion pipeline...")

            with _online_hf_access():
                pipe = StableVideoDiffusionPipeline.from_pretrained(
                    str(model_dir),
                    torch_dtype=dtype,
                    local_files_only=True,
                    use_safetensors=True,
                )

            pipe = pipe.to(device)
            if hasattr(pipe, "enable_attention_slicing"):
                pipe.enable_attention_slicing()
            if hasattr(pipe.unet, "enable_forward_chunking"):
                pipe.unet.enable_forward_chunking(chunk_size=1, dim=1)
            if hasattr(pipe, "enable_vae_slicing"):
                pipe.enable_vae_slicing()
            if hasattr(pipe, "enable_vae_tiling"):
                pipe.enable_vae_tiling()

            _PIPE = pipe
            _PIPE_DEVICE = device
            _PIPE_ERROR = None
            return _PIPE
        except Exception as exc:
            _PIPE_ERROR = str(exc)
            raise


def _prepare_conditioning_image(source_img):
    from PIL import Image

    if not isinstance(source_img, Image.Image):
        source_img = Image.fromarray(source_img)
    source_img = source_img.convert("RGB")
    return source_img.resize((1024, 576), Image.LANCZOS)


def _clear_device_cache() -> None:
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        import torch.mps

        torch.mps.empty_cache()


def _generate_video(
    source_img,
    *,
    num_frames: int,
    num_inference_steps: int,
    fps: int,
    motion_bucket_id: int,
    noise_aug_strength: float,
    min_guidance_scale: float,
    max_guidance_scale: float,
    seed: int,
    progress: gr.Progress | None = None,
):
    from diffusers.utils import export_to_video

    pipe = _load_pipeline(progress=progress)
    image = _prepare_conditioning_image(source_img)
    _VIDEO_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _VIDEO_OUTPUTS_DIR / f"svd_{uuid.uuid4().hex[:12]}.mp4"

    generator_device = _PIPE_DEVICE if _PIPE_DEVICE in {"cuda", "mps"} else "cpu"
    try:
        generator = torch.Generator(device=generator_device).manual_seed(int(seed))
    except Exception:
        generator = torch.Generator(device="cpu").manual_seed(int(seed))

    _clear_device_cache()
    if progress:
        progress(0.12, desc="Generating video frames with Stable Video Diffusion...")

    frames = pipe(
        image,
        decode_chunk_size=1,
        generator=generator,
        num_frames=int(num_frames),
        num_inference_steps=int(num_inference_steps),
        fps=int(fps),
        motion_bucket_id=int(motion_bucket_id),
        noise_aug_strength=float(noise_aug_strength),
        min_guidance_scale=float(min_guidance_scale),
        max_guidance_scale=float(max_guidance_scale),
    ).frames[0]

    export_to_video(frames, str(output_path), fps=int(fps))
    _clear_device_cache()
    return output_path, frames[len(frames) // 2]


def image_to_video_generator(
    source_img,
    num_frames,
    num_inference_steps,
    fps,
    motion_bucket_id,
    noise_aug_strength,
    min_guidance_scale,
    max_guidance_scale,
    seed,
    progress=gr.Progress(),
):
    if source_img is None:
        yield None, None, "Please upload an image.", gr.update(interactive=True, value="Generate Video")
        return

    yield None, None, "Preparing Stable Video Diffusion...", gr.update(interactive=False, value="Rendering...")

    try:
        output_path, preview_frame = _generate_video(
            source_img,
            num_frames=int(num_frames),
            num_inference_steps=int(num_inference_steps),
            fps=int(fps),
            motion_bucket_id=int(motion_bucket_id),
            noise_aug_strength=float(noise_aug_strength),
            min_guidance_scale=float(min_guidance_scale),
            max_guidance_scale=float(max_guidance_scale),
            seed=int(seed),
            progress=progress,
        )
    except Exception as exc:
        detail = _PIPE_ERROR or str(exc)
        yield None, None, f"Video generation failed: {detail}", gr.update(interactive=True, value="Generate Video")
        return

    yield str(output_path), preview_frame, f"Video ready: {output_path.name}", gr.update(interactive=True, value="Generate Video")


def run_image_to_video_api(payload: ImageToVideoApiRequest) -> dict[str, object]:
    from PIL import Image

    source_path = Path(payload.source_image_path).expanduser().resolve()
    output_path = Path(payload.output_video_path).expanduser().resolve()

    if not source_path.exists():
        raise FileNotFoundError(f"Source image not found: {source_path}")

    source_img = Image.open(source_path).convert("RGB")
    rendered_path, _ = _generate_video(
        source_img,
        num_frames=payload.num_frames,
        num_inference_steps=payload.num_inference_steps,
        fps=payload.fps,
        motion_bucket_id=payload.motion_bucket_id,
        noise_aug_strength=payload.noise_aug_strength,
        min_guidance_scale=payload.min_guidance_scale,
        max_guidance_scale=payload.max_guidance_scale,
        seed=payload.seed,
        progress=None,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered_path.replace(output_path)
    return {
        "status": "succeeded",
        "output_video_path": str(output_path),
        "message": "ok",
    }


def _apply_motion_preset(preset_name: str):
    presets = {
        "Balanced": (14, 20, 7, 140, 0.05, 1.0, 3.0),
        "Running Action": (14, 22, 8, 185, 0.06, 1.2, 3.2),
        "Gentle Motion": (12, 18, 7, 110, 0.03, 1.0, 2.4),
        "High Energy": (18, 24, 9, 205, 0.08, 1.2, 3.5),
    }
    values = presets.get(preset_name, presets["Balanced"])
    return tuple(gr.update(value=value) for value in values)


def build_image_to_video_ui(get_navbar) -> gr.Blocks:
    with gr.Blocks(title="Image to Video") as demo:
        gr.HTML(get_navbar("image-to-video"))
        gr.Markdown("# AI Image to Video")
        gr.Markdown("## Stable Video Diffusion Live Module")
        gr.Markdown(
            "This page uses local Stable Video Diffusion. First run downloads the model into the shared model store, "
            "then generates a short image-conditioned clip."
        )

        with gr.Row():
            with gr.Column():
                video_source_in = gr.Image(label="Source Image", type="numpy")
                preset = gr.Dropdown(
                    ["Running Action", "Balanced", "Gentle Motion", "High Energy"],
                    value="Running Action",
                    label="Motion Preset",
                )
                num_frames = gr.Slider(8, 25, value=14, step=1, label="Frame Count")
                num_inference_steps = gr.Slider(10, 30, value=20, step=1, label="Inference Steps")
                fps = gr.Slider(6, 12, value=7, step=1, label="FPS")
                motion_bucket_id = gr.Slider(80, 220, value=140, step=1, label="Motion Strength")
                noise_aug_strength = gr.Slider(0.01, 0.2, value=0.05, step=0.01, label="Creative Drift")
                min_guidance_scale = gr.Slider(1.0, 2.5, value=1.0, step=0.1, label="Min Guidance")
                max_guidance_scale = gr.Slider(1.0, 4.0, value=3.0, step=0.1, label="Max Guidance")
                seed = gr.Number(value=42, label="Seed", precision=0)
                video_run_btn = gr.Button("Generate Video", variant="primary")
                video_status_out = gr.Textbox(label="Status", interactive=False, container=False)
            with gr.Column():
                video_preview_out = gr.Image(label="Preview Frame", interactive=False)
                video_out = gr.Video(label="Generated Video", interactive=False)

        preset.change(
            fn=_apply_motion_preset,
            inputs=[preset],
            outputs=[
                num_frames,
                num_inference_steps,
                fps,
                motion_bucket_id,
                noise_aug_strength,
                min_guidance_scale,
                max_guidance_scale,
            ],
        )

        video_run_btn.click(
            fn=image_to_video_generator,
            inputs=[
                video_source_in,
                num_frames,
                num_inference_steps,
                fps,
                motion_bucket_id,
                noise_aug_strength,
                min_guidance_scale,
                max_guidance_scale,
                seed,
            ],
            outputs=[video_out, video_preview_out, video_status_out, video_run_btn],
            show_progress="hidden",
        )

    return demo
