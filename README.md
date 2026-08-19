# Try-On Studio

Standalone local virtual try-on app built on top of CatVTON, Diffusers, and a shared local model vault.

This repository is the installable application layer. It is not a generic CatVTON training repo and it does not expose the full upstream workflow surface.

## Design System Boundary

This app is informed by [`sovereignsquad/general-design-system`](https://github.com/sovereignsquad/general-design-system), but it does not consume the Mantine packages directly.

Current boundary:

- upstream GDS is the governance and design-authority source
- this repo is still a `Gradio + FastAPI` surface, not a Mantine application
- relevant GDS principles are adopted locally through:
  - [studio_tools/static/global.css](studio_tools/static/global.css:1)
  - [studio_tools/templates/navbar.html](studio_tools/templates/navbar.html:1)
  - [studio_tools/templates/landing.html](studio_tools/templates/landing.html:1)

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
- `GET /api/local-ai/services`
- `GET /api/local-ai/model-packs`
- `POST /api/local-ai/jobs`
- `POST /api/local-ai/garments/isolate`
- `POST /api/local-ai/product-photo/cleanup`
- `POST /api/local-ai/quality/brand-safety`
- `POST /api/local-ai/quality/tryon-gate`
- `POST /api/local-ai/editing/inpaint`
- `POST /api/local-ai/variants/generate`
- `POST /api/local-ai/events/{eventId}/social-stills`
- `GET /api/local-ai/reports`
- the worker status endpoint
- `GET /api/worker/settings`
- the worker settings endpoint
- the worker service-action endpoint
- the worker job retry endpoint
- the setup listing endpoint
- the setup selection endpoint
- `POST /upload_garment`
- `POST /save_package`

The local queue worker surface is:

- `./.venv311/bin/tryon-queue-worker scripts/tryon_queue_worker.py`
- `./.venv311/bin/tryon-queue-worker scripts/tryon_queue_worker.py --once`

The local AI services CLI surface is:

- `./.venv311/bin/python scripts/local_ai_services.py list`
- `./.venv311/bin/python scripts/local_ai_services.py model-packs`
- `./.venv311/bin/python scripts/local_ai_services.py run <service_id> --payload payload.json`
- `./.venv311/bin/python scripts/local_ai_services.py fixtures`
- `./.venv311/bin/python scripts/local_ai_services.py report`

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

- `.config/settings.json`

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
3. `scripts/tryon_queue_worker.py` polls Atlas, claims a queued job, downloads the source image and the Camera-hosted garment asset, and renders via the resolved provider. Garment-typed jersey/top/bottom jobs on a Segmind setup are rerouted to FASHN v1.6 (fal); motorsport suits and local/google setups keep their pipeline. Provider inputs travel as base64 (fal data-URI, Segmind raw) — no ImgBB round-trip on the input path. Only local/motogp profiles call `POST /api/tryon/run`.
4. The worker uploads the generated RESULT to ImgBB (results only; inputs are base64).
5. The worker persists upload state, calls Camera’s internal completion endpoint, and only then marks the queue row `done`.
6. Camera admins review and approve/reject the result before it becomes share-visible or slideshow-eligible.

Operational behavior:

- Atlas is the source of truth for queued, retrying, and completed jobs
- the local Mac can be offline; queued work remains in Atlas and is picked up later
- the worker maintains lease heartbeats, stale-lease recovery, and local runtime diagnostics
- the worker is intended to run as a macOS `launchd` service
- the local app and queue worker are installed as one always-on service pair
- the worker claims jobs FIFO and only checks local render-server readiness inside the local/google-edge render branches; Segmind and fal jobs dispatch without a readiness gate (a job claimed while models load burns an attempt and lands in retry)
- the worker is single-instance; starting another worker exits cleanly instead of creating parallel queue consumers
- the try-on API itself is single-task; a second generation request is rejected while one job is rendering

Event-level setup selection (recommended):

1. Camera operators change setup once per camera in UI.
2. Camera app writes the selected `setupId` to `camera_setup_preferences`.
3. `scripts/tryon_queue_worker.py` resolves setup for each job in order: explicit `request.setupId`, then per-camera preference from `camera_setup_preferences`, then global default.
4. Atlas stores only setup selection metadata (`setupId`, name, defaults, ranking); setup payloads live in local catalog file (`.config/tryon_setups.json`).
5. If a setup is unknown locally, the job falls back to the local fallback profile.

Garment-type render resolution (try-on#37): when a job carries
`request.garmentType` (snapshotted from the garment catalog by Camera —
camera#115), the garment's own type overrides the setup preset's `category`
across all three providers (`motorsport_suit → Full-Body/dresses/one-pieces`,
`jersey`/`top → Upper/tops`, `bottom → Lower/bottoms`), and
`request.sleeveStyle` maps onto the local pipeline's `sleeve_length`. Jobs
without the field (everything created before camera#115) resolve from the
setup exactly as before. Every dispatch logs one line —
`[tryon-worker] job=<id> category=... sleeve=... mask=... source=garment_type|setup` —
so a wrong render is diagnosable from the worker log alone.

Two-piece outfits (try-on#39, local provider only): a job whose
`request.outfitBottomLeatherSuitId` is set renders as two sequential local
passes — the top (`request.leatherSuitId`, must be a `top`-type garment,
Upper category) on the person photo, then the bottom (must be `bottom`-type,
Lower category) on pass 1's output. The order is fixed top-first and the job
is atomic: one result, no intermediate ever published, any pass failing
retries the whole job. Type/provider violations fail fast with named
`outfit_*` errors before any render spend. Each pass logs
`[tryon-worker] job=<id> pass=<n> garment=<id> category=... took=<s>`.

Bare arms for sleeveless garments (try-on#38, local provider only): a
`jersey`/`top` with `sleeveStyle='sleeveless'` renders with the local
pipeline's `mask_mode='expose_arms'` — the arm regions stay INSIDE the edit
mask so the model synthesizes bare skin over any sleeves in the source
photo. This is deliberately the inversion of `sleeve_length='sleeveless'`,
whose historical shrink semantics (preserve already-bare arms) remain
untouched for every legacy setup and non-garment-typed job; the two are
mutually exclusive by construction. Hands and face keep their hard
protection in every mode. Catalog garments truthfully: a garment image that
shows sleeves but is catalogued sleeveless gives the model contradictory
conditioning. Verify quality per photo with
`scripts/ab_render_expose_arms.py --person <img> --garment <img>` (renders
default vs. expose_arms side by side with timings) before enabling a new
jersey for an event.

Required environment variables. Provider names are genericized here — `EXTERNAL_PROVIDER_*`
and `OPTIONAL_PROVIDER_*` are placeholders, not variables the worker reads. Copy
`.env.tryon-worker.example` for the literal names, and note that
`TRYON_POLL_INTERVAL_SECONDS` is documentation only: the poll interval is held in the
worker settings store and changed via the Worker Control page.

```bash
MONGODB_ATLAS_URI=...
MONGODB_DB_NAME=...
IMGBB_API_KEY=...
CAMERA_TRYON_COMPLETE_URL=...
CAMERA_TRYON_INTERNAL_SECRET=...
TRYON_SETUP_COLLECTION=tryon_setups
TRYON_CAMERA_SETUP_PREFERENCE_COLLECTION=camera_setup_preferences
TRYON_SETUP_CATALOG_PATH=.config/tryon_setups.json
TRYON_DEFAULT_SETUP_ID=...
TRYON_QUEUE_ROOT=...
TRYON_SUIT_ASSET_ROOT=...
TRYON_LOCAL_API_URL=...
EXTERNAL_PROVIDER_API_URL=...
EXTERNAL_PROVIDER_API_KEY=...
TRYON_ALLOWED_PERSON_SOURCE_HOSTS=i.ibb.co
TRYON_ALLOWED_SUIT_SOURCE_HOSTS=i.ibb.co
TRYON_MAX_SOURCE_IMAGE_BYTES=26214400
TRYON_MAX_SUIT_IMAGE_BYTES=26214400
TRYON_ALLOW_REDIRECTS=false
EXTERNAL_PROVIDER_TIMEOUT_SECONDS=300
OPTIONAL_PROVIDER_KEY=...
OPTIONAL_PROVIDER_BASE_URL=...
OPTIONAL_PROVIDER_MODEL=...
OPTIONAL_PROVIDER_TIMEOUT_SECONDS=300
TRYON_POLL_INTERVAL_SECONDS=60
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

Install and refresh both services with one command:

```bash
chmod +x scripts/bootstrap_local_services.sh
./scripts/bootstrap_local_services.sh
```

Useful manual service operations:

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
- `workerRunning=true` and `workerJobActive=false` means the service is healthy and idle
- process lists should show `tryon-app-server` and `tryon-queue-worker`, not anonymous `bash`/`python` service entries
- local lock files live under `.runtime/locks/` and are runtime state, not source files

Setup metadata in Atlas + local catalog:

1. `tryon_setups` collection documents now store setup metadata only (not full tuning payload).

```json
{
  "setupId": "default_setup",
  "name": "Default Local",
  "description": "Default high-detail leather route",
  "cameraId": null,
  "active": true,
  "isDefault": true,
  "rank": 0,
  "revision": "local-high-v1",
  "createdAt": "2026-06-03T12:00:00Z",
  "updatedAt": "2026-06-03T12:00:00Z"
}
```

2. `.config/tryon_setups.json` now holds the full payload (`config`) used by local worker run:

```json
{
  "setupId": "default_setup",
  "name": "Local High (Default)",
  "active": true,
  "revision": "local-high-v1",
  "config": {
    "processing_profile": "local_profile",
    "category": "Upper (T-Shirts, Hoodies)",
    "steps": 60,
    "guidance": 4.6
  }
}
```

2. `camera_setup_preferences` stores last selected setup per camera.

```json
{
  "cameraId": "camera_123",
  "setupId": "default_setup",
  "updatedAt": "2026-06-03T12:00:00Z"
}
```

3. Camera job should only pass lean payload:

```json
{
  "schemaVersion": 1,
  "jobId": "job_001",
  "status": "queued",
  "stage": "queued",
  "source": {
    "submissionId": "sub_001",
    "cameraId": "camera_123",
    "imageUrl": "https://..."
  },
  "request": {
    "leatherSuitId": "suit_42"
  }
}
```

4. If a specific image must force a setup, add `request.setupId`.

```json
{
  "request": {
    "leatherSuitId": "suit_42",
    "setupId": "setup_motogp_soft"
  }
}
```

API:

- `GET /api/tryon/setups?cameraId=<cameraId>` returns active setups filtered for the camera and global defaults.
  - Metadata and names come from Atlas (`tryon_setups`).
  - Config values come from `.config/tryon_setups.json` on the try-on machine.
- the setup selection endpoint accepts `{ "cameraId": "camera_123" }` and writes preference.
  - Selected setup is validated against local catalog and recorded both in preference and setup metadata collection.

Camera completion callback is enriched with resolved setup metadata:

- `resolvedSetupId`
- `setupSource` (`job.assigned`, `camera.last`, `global.default`, `legacy`)
- resolved processing profile

Important suit-asset boundary:

- Camera is now the primary owner of uploaded leather-suit assets.
- The worker downloads the suit image from the `leather_suits` record first.
- `TRYON_SUIT_ASSET_ROOT` remains only as a legacy fallback for older suit rows without a Camera-hosted asset URL.

Canonical Atlas queue and suit contracts are documented in `docs/TRYON_ATLAS_CONTRACT.md`.

Queue rows are validated before processing. Legacy rows without `schemaVersion` are normalized to the current in-memory shape, but missing required fields still fail with stable validation errors.

Result publication is restart-safe: once a public result URL exists on the job, retries reuse it and continue with the Camera completion callback instead of uploading another copy.

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
./.venv311/bin/python scripts/audit_models.py --write-manifest
```

Plan or run deterministic syncs from the shared-vault contract:

```bash
./.venv311/bin/python scripts/sync_models.py --profile core --plan
./.venv311/bin/python scripts/sync_models.py --profile core --write-manifest
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

Garment packages use schema version `1`. The runtime can use a saved package by passing `garment_package_name` to the try-on API instead of `garment_image_path`.

Linux support boundaries and smoke validation are documented in `docs/LINUX_SUPPORT.md`.

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
  "processing_profile": "local_profile",
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
  "processing_profile": "local_profile",
  "quality_validation": {},
  "metadata_path": "/abs/path/result.png.json"
}
```

If `show_mask=true`, the API also writes and returns a sibling mask image path.

Processing profiles are resolved from private worker configuration. Keep provider names, payload fields, model IDs, tuning parameters, and preset details out of committed documentation.

External provider prerequisites:

- provider endpoint configured privately when enabled
- provider key configured privately when enabled
- timeout tuned for expected latency
- fallback behavior documented in private ops notes


## Operations Playbooks

### How to add models

Use the shared model vault so app and worker resolve the same assets and checkpoint locations.

1. Place model files under `TRYON_MODELS_ROOT` using the canonical structure.
2. Keep model names aligned with runtime expectations in `model_paths.py`.
3. Validate the vault state and generate a manifest:

```bash
./.venv311/bin/python scripts/audit_models.py --write-manifest
```

4. Run sync operations whenever the vault changes:

```bash
./.venv311/bin/python scripts/sync_models.py --profile core --plan
./.venv311/bin/python scripts/sync_models.py --profile core --write-manifest
```

5. Restart app and worker and verify with `GET /api/capabilities`.

Model ownership checklist:

1. Confirm model family placement before launch:
   - `processors/catvton-segmentation` (SCHP + DensePose checkpoints)
   - `checkpoints/sd15-inpainting`
   - `vae/sd15-vae-ft-mse`
   - `processors/face-restoration`
2. Confirm permissions and ownership so the app process can read all paths.
3. Never keep duplicate copies in different top-level folders; pin to one canonical source.
4. If adding a new family, add a profile contract first (sync + manifest first), then update startup env only after dry-run checks pass.

Post-change verification:

1. Run:

```bash
ls -la "${TRYON_MODELS_ROOT:-/Users/Shared/Models}"
```

2. Confirm manifest update:

```bash
cat "${TRYON_MODELS_ROOT:-/Users/Shared/Models}/manifest.json"
```

3. Generate a one-off inference smoke run with a known fixture:

```bash
./.venv311/bin/python -m pytest -q tests/test_model_sync.py tests/test_worker_contracts.py
```

`install.sh` provisions `pytest`, so this runs in a stock environment. The full suite is `./.venv311/bin/python -m pytest -q tests`.

### How to manage APIs

Keep runtime keys and endpoints in `.env.tryon-worker` and rotate them when ownership changes.

1. Maintain base queue/auth settings in the worker environment file.
2. Configure external providers only when their private setup profile is enabled.
3. Keep provider endpoints, model IDs, and API keys out of committed docs.
4. Verify source download and callbacks with:

```bash
./.venv311/bin/python scripts/verify_tryon_worker_setup.py
```

5. Confirm provider status and fallback behavior from the worker status endpoint.

Provider fallback behavior (safe defaults):

- If an external provider is enabled and healthy, its configured setup may use it.
- If the preferred provider is missing or fails repeatedly, worker falls back according to private setup policy.
- If no external provider is available, worker falls back to the configured local pipeline.

When rotating secrets:

1. Update `.env.tryon-worker` and restart worker immediately after.
2. Invalidate old credentials in provider dashboards where possible.
3. Confirm no active jobs are using stale tokens by watching `processing.stage` transitions in Atlas.

Common API-related failure checks:

- worker logs for provider auth, provider API, and media upload failures
- app logs: provider profile fallback or startup warning events
- the worker status endpoint should still return a running state.

Network and host hardening:

1. Keep host allowlists narrow.
2. Keep `TRYON_ALLOW_REDIRECTS=false` unless a specific proxy/host flow requires redirects.
3. Keep max bytes consistent with camera upload limits to avoid partial reads.

### How to improve presets

Edit `.config/tryon_setups.json` and keep one behavior change per revision.

1. Keep `setupId`, `provider`, `rank`, and `isDefault` intention clear.
2. For local presets, adjust only `steps`, `guidance`, `mask_sharpness`, `mask_padding`, and profile flags.
3. For transparent PNG-safe behavior, apply these guardrails before changing anything else:
   - Use `mask_sharpness >= 15` to reduce feather-induced halos.
   - Keep `mask_padding <= 4` to avoid background expansion into transparent cloth edges.
   - Keep `detail_boost <= 0.3` unless a human review explicitly approves stronger detail restoration.
4. For online presets, encode brand-preservation requirements in `garment_des` with hard constraints:
   no halo, no transparent fill, exact logos/text placement, edge preservation.
5. Add explicit alpha-safe language when garments use transparent PNG boundaries.
6. Run a quick validation set before rollout:
   - opaque garment control
   - transparent garment with anti-aliased edges
   - transparent garment with logos/text
7. Increase `revision` for every meaningful tweak.
8. Record a short changelog line in commit message (e.g. "preset: tighten alpha-safe mask bounds").
9. If changing multiple presets, bump all touched presets in one atomic release review.

Preset shape reference:

```json
{
  "setupId": "example_preset",
  "provider": "local",
  "name": "Example Preset",
  "description": "Use for high-contrast transparent tops.",
  "active": true,
  "isDefault": false,
  "rank": 30,
  "revision": "example-v2",
  "config": {
    "processing_profile": "local_profile",
    "steps": 72,
    "guidance": 4.8,
    "mask_sharpness": 18,
    "mask_padding": 4,
    "garment_des": "Preserve garment details and avoid alpha fill."
  }
}
```

Tuning guidance:

- Local profile changes should be validated with representative source and garment edge cases.
- External profile changes should be validated with diverse logo/text garments.
- Keep detailed prompt and provider tuning notes in private ops documentation.

Rollback approach:

1. Revert `revision` to prior value for affected presets.
2. Restart app/worker to force re-sync to Atlas metadata.
3. Keep the previous working setup pinned in `camera_setup_preferences` while investigating.

### How to update MongoDB Atlas presets

Atlas stores metadata (`tryon_setups`) while this repo keeps full tuning payload in `.config/tryon_setups.json`.

1. Edit `.config/tryon_setups.json` and update each changed preset’s `revision`.
2. Reload and sanity-check catalog JSON before rollout:

```bash
./.venv311/bin/python - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path('.config/tryon_setups.json').read_text(encoding='utf-8'))
assert isinstance(payload, list)
assert all('setupId' in item and 'config' in item for item in payload)
print(f'Loaded {len(payload)} setup entries')
PY
```

3. Restart app and worker so startup sync writes latest metadata to Atlas.
4. Verify via API:

```bash
curl "http://127.0.0.1:7860/api/tryon/setups?cameraId=<cameraId>"
```

5. Confirm Atlas collections:
- `tryon_setups`: expected `setupId`, `name`, `isDefault`, `rank`, `revision`.
- `camera_setup_preferences`: current setup pointer per camera.
6. Pin setup choice for a camera from API:

```bash
curl -X POST "http://127.0.0.1:7860/api/tryon/setups/<setupId>/use" \
  -H "Content-Type: application/json" \
  -d '{"cameraId": "camera_123"}'
```

7. Roll out gradually: one camera cohort at a time and compare results before broader adoption.

Collection-level contract summary:

- `tryon_setups`: one document per `setupId`, including metadata used for UI ranking/defaulting.
- `camera_setup_preferences`: one document per `cameraId`, storing most recent selected setup.
- `tryon_jobs`: per job snapshot for execution path and resolved setup profile.

Suggested Atlas update procedure:

1. Edit [`.config/tryon_setups.json`](.config/tryon_setups.json:1).
2. Bump all touched revisions.
3. Restart both app + worker.
4. Confirm sync from app/worker logs (setup upsert events).
5. Verify via API for one representative camera:

```bash
curl "http://127.0.0.1:7860/api/tryon/setups?cameraId=<cameraId>"
```

6. Verify in Atlas:

- `setupId`, `isDefault`, `rank`, `revision` in `tryon_setups`
- correct active setup in `camera_setup_preferences`
- no stale `setupId` references in in-flight jobs.

Disaster recovery:

1. Keep legacy fallback metadata intact so worker can continue processing.
2. If a bad sync propagates, temporarily pin to local fallback setup in camera or worker route while correcting catalog JSON.
3. Re-deploy corrected JSON and rerun verification steps.

### the worker status endpoint

Returns the current worker runtime snapshot, saved worker settings, recent structured worker events, and queue counts when Atlas credentials are available.

Sample fields:

- `workerRunning`: local worker loop process is active.
- `workerJobActive`: worker is actively processing a job, backed by heartbeat freshness and current job id.
- `queueCounts`: per-state queue cardinalities from Atlas (`queued`, `claimed`, `processing`, `uploading_result`, `notifying_camera`, `retry_wait`, `done`, `failed`).
- `services`: app and worker process state from launchctl/pid checks.

`workerRunning=true` with `workerJobActive=false` means the worker service is idle.

### `GET /api/worker/settings`

Returns persisted worker settings from `.config/worker_settings.json`.

### the worker settings endpoint

Updates persisted worker settings.

Example:

```json
{
  "enabled": true,
  "pollIntervalSeconds": 300,
  "updatedBy": "local-operator"
}
```

### the worker job retry endpoint

Moves a retryable job back into processing flow.

Request body:

```json
{
  "target": "queued",
  "delayMinutes": 0,
  "requestedBy": "local-operator",
  "resetAttempts": false
}
```

- `target` must be `queued` or `retry_wait`.
- `delayMinutes` is allowed only when `target=retry_wait` and must be `0` to `1440`.
- `requestedBy` is optional operator metadata, defaults to `local-operator`.
- `resetAttempts` clears `processing.attemptCount` when `true`.

Rules:

- Returns `409` when the job is active (`claimed`, `processing`, `uploading_result`, `notifying_camera`), or when status is not retryable.
- Returns `404` when jobId is not found.
- Returns `503` when Atlas credentials are missing/unavailable.
- Clears transient failure markers (`error`, `processing.lastError`, `processing.publicationError`) and lease/heartbeat fields before re-queueing.
- Logs worker event `job_retried`.

Example:

```bash
curl -X POST "http://127.0.0.1:7860/api/worker/jobs/job_20260605121640_9d55b24b/retry" \
  -H "Content-Type: application/json" \
  -d '{"target":"retry_wait","delayMinutes":10,"requestedBy":"operator","resetAttempts":true}'
```

### `POST /upload_garment`

Uploads a garment image into `studio_tools/uploads`.

### `POST /save_package`

Writes a sanitized package folder under `studio_tools/packages`.

## Repo Map

High-value application files:

- [app.py](app.py:1) main runtime, routes, UI, and API
- [install.sh](install.sh:1) environment and core-model installer
- [run.sh](run.sh:1) local launcher
- [model_paths.py](model_paths.py:1) shared path and settings helpers
- [scripts/audit_models.py](scripts/audit_models.py:1) shared-vault audit and manifest generator
- [scripts/sync_models.py](scripts/sync_models.py:1) deterministic core model sync
- [services/capabilities.py](services/capabilities.py:1) feature capability contract and status report
- [services/quality_contracts.py](services/quality_contracts.py:1) output quality gates and response metadata contract
- [warp_repair.py](warp_repair.py:1) texture/logo restoration pass
- [studio_tools/generate_master_map.py](studio_tools/generate_master_map.py:1) DensePose master-map generation

## Known Limits

- The app is local-first, not stateless or multi-user.
- The runtime is optimized around Apple Silicon and local desktop use, not cloud deployment.
- **Throughput is memory-bound on a 16 GB machine.** A 768x1024 / 50-step render measured
  ~52 minutes with the machine idle, and 92 minutes with a second image model resident,
  against the ~2-2.5 s/step the silicon is capable of. The gap is paging, not compute.
  Keep other model servers unloaded while rendering, and prefer ~28 steps.
- No larger try-on model is viable here: FLUX.2 klein 4B measured a 17.94 GB peak on a
  16 GB machine. See `docs/LOCAL_TRYON_MODEL_RESEARCH.md` for the survey and numbers.

## Model Attribution

This app builds on work released under non-commercial terms and passes on their
obligations:

- [CatVTON](https://huggingface.co/zhengchong/CatVTON) - CC BY-NC-SA 4.0
- Stable Diffusion 1.5 inpainting - see the upstream model card for its terms
- [GFPGAN](https://github.com/TencentARC/GFPGAN) - optional face restoration

Derivative model weights, if ever produced, must carry the same licence as their source.

## Upstream Reference

This app vendors and adapts CatVTON components, but the root README now documents only the standalone application in this repository.

Upstream CatVTON references:

- [CatVTON repo](https://github.com/Zheng-Chong/CatVTON)
- [CatVTON paper](https://arxiv.org/abs/2407.15886)
- [CatVTON Hugging Face](https://huggingface.co/zhengchong/CatVTON)

Vendored upstream docs under `vendor/` are preserved as third-party reference material and are not the runtime contract for this app.

## Critical Infrastructure Operations

The worker ships with a production reliability layer documented in `docs/TRYON_CRITICAL_INFRASTRUCTURE.md`.

Contract versions:

- infrastructure contract: `2026.06-critical-infra-v1`
- try-on API contract: `tryon-api-v1`
- worker pipeline: `1.1.0`

Primary operator commands:

```bash
./.venv311/bin/python scripts/tryon_infra_cli.py status
./.venv311/bin/python scripts/tryon_infra_cli.py reconcile --limit 200
./.venv311/bin/python scripts/tryon_infra_cli.py backfill-failure-notes --limit 500
./.venv311/bin/python scripts/tryon_canary.py
./.venv311/bin/python scripts/tryon_load_benchmark.py --jobs 20 --median-seconds 180
```

Reliability features:

- provider latency/failure scorecard
- provider circuit-breaker and cooldown policy
- provider daily request limits
- queue backpressure reporting by depth and age
- failed-job taxonomy with operator notes
- Atlas reconciliation report for callback/publication mismatches
- service canary status written to `.runtime/canary_status.json`
- dry-run throughput benchmark plan written to `.runtime/load_benchmark_plan.json`

Safe defaults:

- `TRYON_MAX_CONCURRENCY=1`
- backpressure reports overload but does not discard existing jobs
- repeated provider failures open a circuit instead of repeatedly blocking the queue
- second timeout remains final failed according to the existing timeout policy

Relevant environment controls:

```bash
TRYON_MAX_CONCURRENCY=1
TRYON_BACKPRESSURE_ENABLED=true
TRYON_BACKPRESSURE_MAX_READY_JOBS=50
TRYON_BACKPRESSURE_MAX_OLDEST_READY_AGE_SECONDS=3600
TRYON_PROVIDER_FAILURE_THRESHOLD=3
TRYON_PROVIDER_COOLDOWN_SECONDS=900
TRYON_LOCAL_DAILY_LIMIT=10000
SEGMIND_DAILY_LIMIT=500
FAL_DAILY_LIMIT=500
IMGBB_DAILY_LIMIT=2000
CAMERA_CALLBACK_DAILY_LIMIT=5000
```

Maintenance rule: if queue status, provider metrics, failure taxonomy, reconciliation findings, or worker heartbeat fields change, update `docs/TRYON_ATLAS_CONTRACT.md` and `docs/TRYON_CRITICAL_INFRASTRUCTURE.md` in the same commit.

## Code Comments

What a comment here is for, when a docstring is required, and how to check the tree for
comments that no longer match the code: `docs/CODE_COMMENT_STANDARD.md`. It also lists
the code paths that are deliberately dead — disabled by hard overrides, kept on purpose,
and labelled in place.

## Local AI Services

The app now includes a zero-external-cost local image service family.

Docs:

- `docs/LOCAL_AI_SERVICES.md`
- `docs/LOCAL_AI_SERVICES_USER_GUIDE.md`
- `docs/RELEASE_NOTES.md`

Services:

- `garment_isolation`
- `product_photo_cleanup`
- `brand_safety_analyzer`
- `tryon_quality_gate`
- `local_inpainting_cleanup`
- `campaign_variant_generator`
- `event_social_still_builder`
- `synthetic_fixture_generator`
- `local_ai_service_reporting`

All first-version service execution is local. Do not add paid external inference/API dependencies to this lane without updating the architecture docs, README, release notes, privacy/security notes, and GitHub issue acceptance criteria.
