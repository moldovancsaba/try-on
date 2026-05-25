# Try-On Studio

Standalone local virtual try-on app built on top of CatVTON, Diffusers, and a shared local model vault.

This repository is the installable application layer. It is not a generic CatVTON training repo and it does not expose the full upstream workflow surface.

## What Ships

The current app serves these pages from `http://127.0.0.1:7860`:

- `/` landing page
- `/try-on` main virtual try-on UI
- `/motogp-leather-magic` dedicated MotoGP leather-suit workflow
- `/set-garment` garment setup studio
- `/garments` local garment library

The current API surface is:

- `POST /api/tryon/run`
- `GET /api/capabilities`
- `GET /api/quality-contracts`
- `POST /upload_garment`
- `POST /save_package`

## Runtime Summary

Core runtime:

- Python 3.11
- Gradio mounted into FastAPI
- PyTorch with automatic device selection: `cuda`, `mps`, then `cpu`
- CatVTON for try-on
- DensePose + SCHP for body parsing
- Stable Diffusion 1.5 inpainting for the try-on pipeline
- GFPGAN for optional face restoration

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
- [MotoGP Leather Magic](http://127.0.0.1:7860/motogp-leather-magic/)
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
    /try-on
  /vae
    /sd15-vae-ft-mse
  /processors
    /catvton-segmentation
    /face-restoration
    /upscalers
  /adapters
  /loras
  /llms
  /manifest.json
```

Audit the shared vault and refresh the manifest:

```bash
python scripts/audit_models.py --write-manifest
```

Plan or run deterministic syncs from the shared-vault contract:

```bash
python scripts/sync_models.py --profile core --plan
python scripts/sync_models.py --profile core --write-manifest
```

## What `install.sh` Seeds

`install.sh` provisions the Python environment and runs the deterministic shared-vault sync for the core contract:

- `processors/catvton-segmentation`
- `checkpoints/sd15-inpainting`
- `vae/sd15-vae-ft-mse`
- `processors/face-restoration`

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
- optional deep texture restoration using `warp_repair.py`

Important behavior:

- shipped resolution mode is `High Quality` only
- `Fast (Draft)` is disabled
- the wrapper enforces a stronger baseline for high-quality runs
- the app starts serving before model warmup completes; generation is blocked until the loader is ready

### MotoGP Leather Magic

Dedicated page: `/motogp-leather-magic`

This mode is tuned for a narrower input contract:

- full-body A-pose person photo
- front-facing full-body leather suit image
- full-body suit category locked on the page

Runtime defaults are more aggressive than the generic try-on page:

- `Full-Body (Suits, Dresses, Rompers)` locked
- `High Quality` only
- at least `30` steps
- guidance at least `4.2`
- `DPM++ 2M` sampler
- preserved head enabled
- high-fidelity VAE enabled
- deep texture warp disabled by default

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
- `garment_image_path`
- `output_image_path`

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
  "use_vae_hf": true,
  "sampler_name": "Euler A"
}
```

Response:

```json
{
  "status": "succeeded",
  "output_image_path": "/abs/path/result.png",
  "message": "ok",
  "quality_validation": {},
  "metadata_path": "/abs/path/result.png.json"
}
```

If `show_mask=true`, the API also writes and returns a sibling mask image path.

### `POST /upload_garment`

Uploads a garment image into `studio_tools/uploads`.

### `POST /save_package`

Writes a sanitized package folder under `studio_tools/packages`.

## Repo Map

High-value application files:

- [app.py](/Users/Shared/Projects/try-on/app.py:1) main runtime, routes, UI, and API
- [install.sh](/Users/Shared/Projects/try-on/install.sh:1) environment and core-model installer
- [run.sh](/Users/Shared/Projects/try-on/run.sh:1) local launcher
- [model_paths.py](/Users/Shared/Projects/try-on/model_paths.py:1) shared path and settings helpers
- [scripts/audit_models.py](/Users/Shared/Projects/try-on/scripts/audit_models.py:1) shared-vault audit and manifest generator
- [scripts/sync_models.py](/Users/Shared/Projects/try-on/scripts/sync_models.py:1) deterministic core model sync
- [services/capabilities.py](/Users/Shared/Projects/try-on/services/capabilities.py:1) feature capability contract and status report
- [services/quality_contracts.py](/Users/Shared/Projects/try-on/services/quality_contracts.py:1) output quality gates and response metadata contract
- [warp_repair.py](/Users/Shared/Projects/try-on/warp_repair.py:1) texture/logo restoration pass
- [studio_tools/generate_master_map.py](/Users/Shared/Projects/try-on/studio_tools/generate_master_map.py:1) DensePose master-map generation

## Known Limits

- The app is local-first, not stateless or multi-user.
- The runtime is optimized around Apple Silicon and local desktop use, not cloud deployment.

## Upstream Reference

This app vendors and adapts CatVTON components, but the root README now documents only the standalone application in this repository.

Upstream CatVTON references:

- [CatVTON repo](https://github.com/Zheng-Chong/CatVTON)
- [CatVTON paper](https://arxiv.org/abs/2407.15886)
- [CatVTON Hugging Face](https://huggingface.co/zhengchong/CatVTON)

Vendored upstream docs under `vendor/` are preserved as third-party reference material and are not the runtime contract for this app.
