# Try-On Studio

Standalone local try-on, face swap, hold-product, and image-to-video app built on top of CatVTON, Diffusers, and a shared local model vault.

This repository is the installable application layer. It is not a generic CatVTON training repo and it does not expose the full upstream workflow surface.

## What Ships

The current app serves these pages from `http://127.0.0.1:7860`:

- `/` landing page
- `/try-on` main virtual try-on UI
- `/face-swap` standalone face swap UI
- `/hold-product` pose-guided product placement UI
- `/image-to-video` Stable Video Diffusion UI
- `/set-garment` garment setup studio
- `/garments` local garment library

The current API surface is:

- `POST /api/tryon/run`
- `POST /api/image-to-video/run`
- `POST /upload_garment`
- `POST /save_package`

## Runtime Summary

Core runtime:

- Python 3.11
- Gradio mounted into FastAPI
- PyTorch with automatic device selection: `cuda`, `mps`, then `cpu`
- CatVTON for try-on
- DensePose + SCHP for body parsing
- Stable Diffusion 1.5 inpainting for the main try-on and hold-product pipelines
- GFPGAN for optional face restoration
- InsightFace InSwapper for primary face swap
- Stable Video Diffusion for image-to-video

Shared model root:

- Default: `/Users/Shared/Models`
- Override with `TRYON_MODELS_ROOT`

App settings path:

- `/Users/Shared/Projects/try-on/.config/settings.json`

Legacy model-vault settings files are migrated forward automatically on startup.

## Quick Start

Install:

```bash
chmod +x install.sh run.sh
./install.sh
```

Run:

```bash
./run.sh
```

Open:

- [Landing](http://127.0.0.1:7860/)
- [Try-On](http://127.0.0.1:7860/try-on/)
- [Face Swap](http://127.0.0.1:7860/face-swap/)
- [Hold Product](http://127.0.0.1:7860/hold-product/)
- [Image to Video](http://127.0.0.1:7860/image-to-video/)
- [Setup Garment](http://127.0.0.1:7860/set-garment)
- [Garment Library](http://127.0.0.1:7860/garments)

## Model Vault Contract

This app is designed to reuse a centralized shared model store instead of keeping project-local checkpoints.

Default root:

```text
/Users/Shared/Models
```

Current canonical layout:

```text
/Users/Shared/Models
  /.cache/huggingface
  /checkpoints
    /sd15-inpainting
    /stable-video-diffusion-img2vid-xt
    /try-on
  /vae
    /sd15-vae-ft-mse
  /processors
    /catvton-segmentation
    /annotators
    /face-restoration
    /upscalers
  /controlnet
    /sd15-openpose
  /analysis
    /insightface
  /adapters
  /loras
  /llms
  /manifest.json
```

Audit the shared vault and refresh the manifest:

```bash
python scripts/audit_models.py --write-manifest
```

## What `install.sh` Seeds

`install.sh` provisions the Python environment and downloads the core offline try-on stack:

- `processors/catvton-segmentation`
- `checkpoints/sd15-inpainting`
- `vae/sd15-vae-ft-mse`
- `processors/face-restoration/GFPGANv1.4.pth`
- GFPGAN support weights used by the local runtime

It does not currently pre-seed every optional feature dependency.

Optional or first-use dependencies:

- `checkpoints/stable-video-diffusion-img2vid-xt`
  - downloaded on first use by the image-to-video page
- `analysis/insightface`
  - InsightFace may download model assets on first use if they are not already present
- `controlnet/sd15-openpose`
- `processors/annotators`
  - required by the hold-product pipeline; expected to already exist in the shared model vault

If you want a fully pre-seeded offline machine, populate those optional directories before relying on those pages.

## Shipped Features

### Try-On

Main page: `/try-on`

Supported controls:

- garment category
- sleeve and pant cut constraints
- steps, guidance, mask sharpness, mask padding
- detail boost
- clean plate compositing
- seed locking
- HF fine-tuned VAE toggle
- sampler choice: `Euler A`, `DPM++ 2M`, `UniPC`
- optional GFPGAN face restoration
- optional preserved-head literal paste
- optional face swap during try-on
- optional deep texture restoration using `warp_repair.py`

Important behavior:

- shipped resolution mode is `High Quality` only
- `Fast (Draft)` is disabled
- the wrapper enforces a stronger baseline for high-quality runs
- the app starts serving before model warmup completes; generation is blocked until the loader is ready

### Face Swap

Standalone page: `/face-swap`

Primary path:

- InsightFace InSwapper with CoreML or CPU providers

Fallback path:

- if InsightFace cannot detect a target face, the app falls back to the geometric composite path used by the try-on pipeline

Controls:

- sports portrait mode
- include hair / hat / glasses
- blend strength
- optional debug mask output

### Hold Product

Standalone page: `/hold-product`

This is a pose-guided local product-placement workflow that combines:

- editable upper-body pose rig
- optional pose-image override
- OpenPose annotators
- ControlNet OpenPose
- SD15 inpainting

Controls:

- built-in pose templates
- hold mode: `Overhead Trophy` or `Front Hold`
- custom prompt override
- position offsets and scale multiplier
- background removal and edge softness
- preserve product detail
- debug pose map and inpaint mask

### Image to Video

Standalone page: `/image-to-video`

Backend:

- `stabilityai/stable-video-diffusion-img2vid-xt`

Controls:

- motion preset
- frame count
- inference steps
- FPS
- motion strength
- creative drift
- min/max guidance
- seed

Outputs:

- rendered MP4
- preview frame

Generated videos are saved under:

```text
outputs/image_to_video
```

### Garment Studio

Pages:

- `/set-garment`
- `/garments`

Storage under:

```text
studio_tools/packages
studio_tools/uploads
studio_tools/master_maps
```

The package API now uses safer path handling and writes:

- `metadata.json`
- `package.json`
- `garment.png`

inside each package folder.

## API

### `POST /api/tryon/run`

Runs the try-on pipeline and saves the output to a path you provide.

Required fields:

- `person_image_path`
- `output_image_path`

Conditionally required:

- `garment_image_path` unless `face_swap_only=true`
- `face_image_path` when `enable_face_swap=true`

Example:

```json
{
  "person_image_path": "/abs/path/person.png",
  "garment_image_path": "/abs/path/garment.png",
  "output_image_path": "/abs/path/result.png",
  "category": "Upper (T-Shirts, Hoodies)",
  "steps": 24,
  "guidance": 3.5,
  "seed": 42,
  "enable_face_swap": false,
  "use_vae_hf": true,
  "sampler_name": "Euler A"
}
```

Response:

```json
{
  "status": "succeeded",
  "output_image_path": "/abs/path/result.png",
  "message": "ok"
}
```

If `show_mask=true`, the API also writes and returns a sibling mask image path.

### `POST /api/image-to-video/run`

Required fields:

- `source_image_path`
- `output_video_path`

Example:

```json
{
  "source_image_path": "/abs/path/source.png",
  "output_video_path": "/abs/path/output.mp4",
  "num_frames": 14,
  "num_inference_steps": 20,
  "fps": 7,
  "motion_bucket_id": 140,
  "noise_aug_strength": 0.05,
  "min_guidance_scale": 1.0,
  "max_guidance_scale": 3.0,
  "seed": 42
}
```

### `POST /upload_garment`

Uploads a garment image into `studio_tools/uploads`.

### `POST /save_package`

Writes a sanitized package folder under `studio_tools/packages`.

## Repo Map

High-value application files:

- [app.py](/Users/Shared/Projects/try-on/app.py:1) main runtime, routes, UI, and API
- [image_to_video_page.py](/Users/Shared/Projects/try-on/image_to_video_page.py:1) Stable Video Diffusion page and API helper
- [install.sh](/Users/Shared/Projects/try-on/install.sh:1) environment and core-model installer
- [run.sh](/Users/Shared/Projects/try-on/run.sh:1) local launcher
- [model_paths.py](/Users/Shared/Projects/try-on/model_paths.py:1) shared path and settings helpers
- [scripts/audit_models.py](/Users/Shared/Projects/try-on/scripts/audit_models.py:1) shared-vault audit and manifest generator
- [warp_repair.py](/Users/Shared/Projects/try-on/warp_repair.py:1) texture/logo restoration pass
- [studio_tools/generate_master_map.py](/Users/Shared/Projects/try-on/studio_tools/generate_master_map.py:1) DensePose master-map generation

## Known Limits

- The app is local-first, not stateless or multi-user.
- Some optional pages depend on models not installed by `install.sh`.
- `Hold Product` depends on the shared vault having working OpenPose annotators and ControlNet assets.
- `Image to Video` downloads SVD on first use if it is missing.
- The runtime is optimized around Apple Silicon and local desktop use, not cloud deployment.

## Upstream Reference

This app vendors and adapts CatVTON components, but the root README now documents only the standalone application in this repository.

Upstream CatVTON references:

- [CatVTON repo](https://github.com/Zheng-Chong/CatVTON)
- [CatVTON paper](https://arxiv.org/abs/2407.15886)
- [CatVTON Hugging Face](https://huggingface.co/zhengchong/CatVTON)

Vendored upstream docs under `vendor/` are preserved as third-party reference material and are not the runtime contract for this app.
