# try-on API Reference

> Generated for try-on#44, measured against `docs/_audit/endpoints.json`. **Coverage: 31 of 31 routes.**

## Security posture (applies to every route)

The app-server binds **`127.0.0.1:7860`** (loopback only; see `app.py` `uvicorn.run(host="127.0.0.1", port=7860)`). As of the latest commit (`0ddb882`, v12.2.0) an **origin-guard HTTP middleware** (`_origin_guard`, `app.py:1414`) rejects any request carrying a cross-origin `Origin` header with **HTTP 403 `{"detail": "forbidden origin"}`**. Only `http://127.0.0.1:7860` and `http://localhost:7860` are allowed; requests with no `Origin` header (curl, the queue worker's own localhost calls) pass through. In addition, `POST /api/tryon/run` now constrains its render output path to the project root (`app.py:1849-1853`): a write outside the try-on workspace is rejected with HTTP 400.

**There is no per-route authentication.** The protection model for every route below is identical: **loopback bind + origin guard**, plus render-path containment on the one render route. No route checks a token, session, or shared secret.

The two **control routes** that mutate real state — `POST /api/tryon/run` (drives GPU render + filesystem writes) and `POST /api/worker/service-action` (restarts/triggers the queue worker) — **still lack a shared secret**, so any loopback caller with a same-origin (or absent) `Origin` header can invoke them. This gap is tracked in **try-on#42**.

Legend for **Auth** column: `loopback+origin-guard` = the shared model above, no per-route auth. Control routes are flagged.

## Page routes (HTML)

| Method | Path | Auth | Purpose | Side effects |
|---|---|---|---|---|
| GET | `/` | loopback+origin-guard | Landing page | None (renders `landing.html`) |
| GET | `/set-garment` | loopback+origin-guard | Setup-studio page | None (renders `index.html`) |
| GET | `/garments` | loopback+origin-guard | Package library page | Filesystem read (lists `studio_tools/packages`) |
| GET | `/worker-control` | loopback+origin-guard | Worker-control page | None (renders `worker_control.html`) |

## Studio routes

| Method | Path | Auth | Purpose | Side effects |
|---|---|---|---|---|
| POST | `/upload_garment` | loopback+origin-guard | Upload a garment image | Filesystem write to `uploads/` (runtime-replaced by `_safe_upload_garment`, which sanitizes the filename) |
| POST | `/save_package` | loopback+origin-guard | Persist a garment package (json + image copy) | Filesystem write under `studio_tools/packages/` (runtime-replaced by `_safe_save_package`, which path-guards names) |

## Try-on core routes

| Method | Path | Auth | Purpose | Side effects |
|---|---|---|---|---|
| POST | `/api/tryon/run` | loopback+origin-guard — **CONTROL, no shared secret (try-on#42)** | Run a try-on render from filesystem-in/filesystem-out paths | Loads local models; filesystem read of person/garment inputs; filesystem write of output PNG (+ optional mask + sidecar metadata) — **output path constrained to project root** (400 otherwise). No Atlas. |
| GET | `/api/tryon/setups` | loopback+origin-guard | List selectable camera setups | **Not read-only:** pushes local catalog into Atlas `tryon_setups` on every call, then reads it back; reads local setup catalog file |
| POST | `/api/tryon/setups/{setupId}/use` | loopback+origin-guard | Record a camera's chosen setup | Atlas upsert into `tryon_setups` and `camera_setup_preferences` |
| POST | `/api/tryon/jobs/{job_id}/retry` | loopback+origin-guard | Re-queue a job (alias of the worker retry route) | Atlas `tryon_jobs` update; appends worker event to `.runtime` event log |

## Worker routes

| Method | Path | Auth | Purpose | Side effects |
|---|---|---|---|---|
| GET | `/api/worker/status` | loopback+origin-guard | Worker status + queue-count report | Filesystem read of worker status; Atlas `tryon_jobs` count reads |
| GET | `/api/worker/settings` | loopback+origin-guard | Read worker settings | Filesystem read (`.runtime` worker settings) |
| POST | `/api/worker/settings` | loopback+origin-guard | Update worker settings (enabled / poll interval) | Filesystem write of worker settings; appends worker event |
| POST | `/api/worker/service-action` | loopback+origin-guard — **CONTROL, no shared secret (try-on#42)** | Request a worker service action (restart / run_now / …) | Filesystem read of runtime state (409 if a job is active); performs service action; appends worker event |
| POST | `/api/worker/jobs/{job_id}/retry` | loopback+origin-guard | Re-queue a finished/failed job (double-checked against runtime + Atlas) | Atlas `tryon_jobs` update; appends worker event to `.runtime` event log |

## Capability & contract routes

| Method | Path | Auth | Purpose | Side effects |
|---|---|---|---|---|
| GET | `/api/capabilities` | loopback+origin-guard | Report resolved runtime capabilities | None (introspects models/env) |
| GET | `/api/quality-contracts` | loopback+origin-guard | Return quality-contract definitions | None (read-only) |

## Local-AI service routes

All `local-ai` routes dispatch through `run_local_ai_service(_ROOT, …)` (or `service_registry` / `evaluate_model_packs`). Side effects are: local model-pack execution, filesystem reads/writes under `.runtime/local_ai/`, and — depending on the service — calls to the configured external AI providers (e.g. the Google-edge services). None touch Atlas.

| Method | Path | Auth | Purpose | Side effects |
|---|---|---|---|---|
| GET | `/api/local-ai/services` | loopback+origin-guard | List registered local-AI services | Filesystem read (models root) |
| GET | `/api/local-ai/model-packs` | loopback+origin-guard | Evaluate installed model packs | Filesystem read (models root) |
| POST | `/api/local-ai/jobs` | loopback+origin-guard | Run an arbitrary local-AI service by `serviceId` | Local model exec; `.runtime/local_ai` filesystem; possible external provider |
| POST | `/api/local-ai/garments/isolate` | loopback+origin-guard | Run `garment_isolation` service | Local model exec; `.runtime/local_ai` filesystem |
| POST | `/api/local-ai/product-photo/cleanup` | loopback+origin-guard | Run `product_photo_cleanup` service | Local model exec; `.runtime/local_ai` filesystem |
| POST | `/api/local-ai/quality/brand-safety` | loopback+origin-guard | Run `brand_safety_analyzer` service | Local model exec; `.runtime/local_ai` filesystem |
| POST | `/api/local-ai/quality/tryon-gate` | loopback+origin-guard | Run `tryon_quality_gate` service | Local model exec; `.runtime/local_ai` filesystem |
| POST | `/api/local-ai/google-edge/analyze` | loopback+origin-guard | Run `google_edge_analyzer` service | Local model exec; external Google-edge provider; `.runtime/local_ai` filesystem |
| POST | `/api/local-ai/google-edge/tryon` | loopback+origin-guard | Run `google_edge_tryon` service | Local model exec; external Google-edge provider; `.runtime/local_ai` filesystem |
| POST | `/api/local-ai/editing/inpaint` | loopback+origin-guard | Run `local_inpainting_cleanup` service | Local model exec; `.runtime/local_ai` filesystem |
| POST | `/api/local-ai/variants/generate` | loopback+origin-guard | Run `campaign_variant_generator` service | Local model exec; `.runtime/local_ai` filesystem |
| POST | `/api/local-ai/events/{event_id}/social-stills` | loopback+origin-guard | Run `event_social_still_builder` for an event | Local model exec; `.runtime/local_ai` filesystem |
| GET | `/api/local-ai/reports` | loopback+origin-guard | Run `local_ai_service_reporting` | Filesystem read/write under `.runtime/local_ai` |
| GET | `/api/local-ai/reports/export` | loopback+origin-guard | Export service report to CSV | Filesystem write `.runtime/local_ai/reports/local_ai_services.csv` |

## Atlas collections touched (3)

- `tryon_jobs` — job queue state (worker/tryon retry routes, worker status counts)
- `tryon_setups` — setup catalog synced from the local file (setups list/use routes)
- `camera_setup_preferences` — per-camera selected setup (setups/use route)

Collection names are overridable via `TRYON_SETUP_COLLECTION` / `TRYON_CAMERA_SETUP_PREFERENCE_COLLECTION`.
