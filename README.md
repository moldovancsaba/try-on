# Try-On Studio

Standalone local virtual try-on app built on top of CatVTON, Diffusers, and a shared local model vault.

This repository is the installable application layer. It is not a generic CatVTON training repo and it does not expose the full upstream workflow surface.

## Design System Boundary

This app is informed by [`sovereignsquad/general-design-system`](https://github.com/sovereignsquad/general-design-system), but it does not consume the Mantine packages directly.

Current boundary:

- upstream GDS is the governance and design-authority source
- this repo is still a `Gradio + FastAPI` surface, not a Mantine application
- relevant GDS principles are adopted locally through:
  - [studio_tools/static/global.css](/Users/Shared/Projects/try-on/studio_tools/static/global.css:1)
  - [studio_tools/templates/navbar.html](/Users/Shared/Projects/try-on/studio_tools/templates/navbar.html:1)
  - [studio_tools/templates/landing.html](/Users/Shared/Projects/try-on/studio_tools/templates/landing.html:1)

That means “update to latest GDS” in this repository means syncing applicable design rules, navigation/shell patterns, accessibility states, and responsive behavior, not importing Mantine providers or package exports directly.

## What Ships

The current app serves these pages from `http://127.0.0.1:7860`:

- `/` landing page
- `/try-on` main virtual try-on UI
- `/motogp-leather-magic` dedicated MotoGP leather-suit workflow
- `/worker-control` local worker operations surface
- `/set-garment` garment setup studio
- `/garments` local garment library

The current API surface is:

- `POST /api/tryon/run`
- `GET /api/capabilities`
- `GET /api/quality-contracts`
- `GET /api/worker/status`
- `GET /api/worker/settings`
- `POST /api/worker/settings`
- `POST /api/worker/service-action`
- `POST /upload_garment`
- `POST /save_package`

The local queue worker surface is:

- `python scripts/tryon_queue_worker.py`
- `python scripts/tryon_queue_worker.py --once`

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
- [Worker Control](http://127.0.0.1:7860/worker-control/)
- [Setup Garment](http://127.0.0.1:7860/set-garment)
- [Garment Library](http://127.0.0.1:7860/garments)

## Camera Queue Worker

This repository is also the official local worker runtime for Camera try-on jobs.

Flow:

1. Camera saves the normal submission.
2. Camera enqueues a `tryon_jobs` record in MongoDB Atlas.
3. `scripts/tryon_queue_worker.py` polls Atlas, claims a queued job, downloads the source image, downloads the selected Camera-hosted leather suit asset, and calls `POST /api/tryon/run`.
4. The worker uploads the generated image to ImgBB.
5. The worker persists upload state, calls Camera’s internal completion endpoint, and only then marks the queue row `done`.
6. Camera admins review and approve/reject the result before it becomes share-visible or slideshow-eligible.

Operational behavior:

- Atlas is the source of truth for queued, retrying, and completed jobs
- the local Mac can be offline; queued work remains in Atlas and is picked up later
- the worker maintains lease heartbeats, stale-lease recovery, and local runtime diagnostics
- the worker is intended to run as a macOS `launchd` service

Required environment variables:

```bash
MONGODB_ATLAS_URI=...
MONGODB_DB_NAME=...
IMGBB_API_KEY=...
CAMERA_TRYON_COMPLETE_URL=https://camera.example.com/api/internal/tryon/complete
CAMERA_TRYON_INTERNAL_SECRET=...
TRYON_QUEUE_ROOT=/Users/Shared/Projects/try-on/queue
TRYON_SUIT_ASSET_ROOT=/Users/Shared/Projects/try-on/images
TRYON_LOCAL_API_URL=http://127.0.0.1:7860/api/tryon/run
TRYON_ALLOWED_PERSON_SOURCE_HOSTS=i.ibb.co
TRYON_ALLOWED_SUIT_SOURCE_HOSTS=i.ibb.co
TRYON_MAX_SOURCE_IMAGE_BYTES=26214400
TRYON_MAX_SUIT_IMAGE_BYTES=26214400
TRYON_ALLOW_REDIRECTS=false
TRYON_POLL_INTERVAL_SECONDS=300
TRYON_LEASE_DURATION_SECONDS=600
TRYON_MAX_ATTEMPTS=3
```

Recommended setup:

```bash
cp .env.tryon-worker.example .env.tryon-worker
```

Verify the worker contract before running live jobs:

```bash
./.venv311/bin/python scripts/verify_tryon_worker_setup.py
```

Check local service health:

```bash
./.venv311/bin/python scripts/service_healthcheck.py
```

Install the worker as a background service:

```bash
mkdir -p ~/Library/LaunchAgents
cp launchd/com.tryon.camera-worker.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.tryon.camera-worker.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.tryon.camera-worker.plist
launchctl kickstart -k gui/$(id -u)/com.tryon.camera-worker
```

Install the local app server as a background service:

```bash
mkdir -p ~/Library/LaunchAgents
cp launchd/com.tryon.app-server.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.tryon.app-server.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.tryon.app-server.plist
launchctl kickstart -k gui/$(id -u)/com.tryon.app-server
```

Useful service operations:

```bash
launchctl print gui/$(id -u)/com.tryon.camera-worker
launchctl print gui/$(id -u)/com.tryon.app-server
launchctl kickstart -k gui/$(id -u)/com.tryon.camera-worker
launchctl kickstart -k gui/$(id -u)/com.tryon.app-server
```

Operator control notes:

- `/worker-control` now shows both app-service and worker-service state
- the worker can be forced to poll immediately with `Run Worker Now`
- service actions are routed through `launchctl` when the managed plist is installed
- restart or run-now actions are blocked while a queue job is actively processing

Important suit-asset boundary:

- Camera is now the primary owner of uploaded leather-suit assets.
- The worker downloads the suit image from the `leather_suits` record first.
- `TRYON_SUIT_ASSET_ROOT` remains only as a legacy fallback for older suit rows without a Camera-hosted asset URL.

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

Optional high-value fields:

- `processing_profile`
- `category`
- `steps`
- `guidance`
- `preserve_head`
- `sampler_name`

Example:

```json
{
  "person_image_path": "/abs/path/person.png",
  "garment_image_path": "/abs/path/garment.png",
  "output_image_path": "/abs/path/result.png",
  "processing_profile": "motogp_leather_magic",
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
  "processing_profile": "motogp_leather_magic",
  "quality_validation": {},
  "metadata_path": "/abs/path/result.png.json"
}
```

If `show_mask=true`, the API also writes and returns a sibling mask image path.

When `processing_profile=motogp_leather_magic`, the server enforces the full-body MotoGP preset:

- full-body category
- minimum 30 steps
- guidance >= 4.2
- `DPM++ 2M`
- preserved head enabled
- high-fidelity VAE enabled

### `GET /api/worker/status`

Returns the current worker runtime snapshot, saved worker settings, recent structured worker events, and queue counts when Atlas credentials are available.

### `GET /api/worker/settings`

Returns persisted worker settings from `.config/worker_settings.json`.

### `POST /api/worker/settings`

Updates persisted worker settings.

Example:

```json
{
  "enabled": true,
  "pollIntervalSeconds": 300,
  "updatedBy": "local-operator"
}
```

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
