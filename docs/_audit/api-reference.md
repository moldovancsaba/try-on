# try-on API Reference

> Measured against `docs/_audit/endpoints.json`. **Coverage: 31 of 31 routes**, each with
> auth, request shape, response shape, side effects, and caller evidence (try-on#44).
> Route line numbers are from `app.py` at v12.2.1.

## Security posture (applies to every route)

The app-server binds **`127.0.0.1:7860`** (loopback only; `uvicorn.run(host="127.0.0.1",
port=7860)` in `app.py`). An **origin-guard HTTP middleware** (`_origin_guard`) rejects any
request carrying a cross-origin `Origin` header with **403 `{"detail": "forbidden origin"}`**;
only `http://127.0.0.1:7860` and `http://localhost:7860` are allowed, and requests with no
`Origin` header (curl, the queue worker) pass.

The two **control routes** — `POST /api/tryon/run` and `POST /api/worker/service-action` —
additionally require the header **`x-tryon-local-secret`** equal to `TRYON_LOCAL_SECRET`
(`_require_local_secret`, try-on#42, landed in 28a76c2). If the secret is unset the routes
refuse every request with **401**. The secret lives only in `.env.tryon-worker`; the
Worker Control page receives it server-side for its own calls.

`POST /api/tryon/run` also constrains its output path to the project root (400 otherwise).
No other route checks a token, session, or secret: **loopback + origin guard** is the whole
model for the remaining 29 routes.

Legend for **Auth**: `L+O` = loopback + origin guard; `L+O+secret` = plus `x-tryon-local-secret`.

## Page routes (HTML)

| Method | Path | Auth | Request | Response | Side effects |
|---|---|---|---|---|---|
| GET | `/` | L+O | none | `text/html` (`landing.html`) | none |
| GET | `/set-garment` | L+O | none | `text/html` (`index.html`) | none |
| GET | `/garments` | L+O | none | `text/html` (`library.html`, lists package dirs) | fs read `studio_tools/packages` |
| GET | `/worker-control` | L+O | none | `text/html` (`worker_control.html`; the local secret is injected for the page's own POSTs) | none |

## Studio routes

Both handlers are replaced at startup by path-sanitising versions (`_safe_upload_garment`,
`_safe_save_package`, registered via `_replace_fastapi_route`); the shapes below are the
live ones.

| Method | Path | Auth | Request | Response | Side effects |
|---|---|---|---|---|---|
| POST | `/upload_garment` | L+O | multipart form field `file` (image) | `{url: "/uploads/<name>", filename, path}` | fs write `studio_tools/uploads/<sanitised name>` |
| POST | `/save_package` | L+O | JSON `StudioPackageRequest` `{package_name, garment_filename, mannequin_view, pant_length?="default", sleeve_length?="default", keypoints?=[]}` | `{success: true, path}` | fs write `studio_tools/packages/<name>/package.json` + copies the garment as `garment.png` |

## Try-on core routes

| Method | Path | Auth | Request | Response | Side effects |
|---|---|---|---|---|---|
| POST | `/api/tryon/run` | **L+O+secret** | JSON `TryOnApiRequest`: required `person_image_path`, `output_image_path`; one of `garment_image_path` / `garment_package_name`; optional `processing_profile="generic"`, `category="Upper"`, `category_source="setup"`, `mask_mode="default"`, `sleeve_length`, `pant_length`, `resolution="High Quality"`, `steps=24`, `guidance=3.5`, `seed=42`, `show_mask=false`, `mask_sharpness=12`, `mask_padding=6`, `detail_boost=0`, `face_restore_strength=0`, `preserve_head=false`, `lock_seed=true`, `use_vae_hf=true`, `sampler_name="Euler A"`, `composite_strength=0`; `enable_deep_texture`/`warp_strength` accepted and ignored | `{status: "succeeded", output_image_path, message, processing_profile, mask_image_path?, quality_validation: {passed, failures[], warnings[], metrics}, metadata_path}`; 400 bad path/mask mode, 401 secret, 409 render busy, 500 quality gate failed | loads local models; fs read of inputs; fs write of output PNG (+ `__mask` PNG, + `.json` sidecar). No Atlas |
| GET | `/api/tryon/setups` | L+O | query `cameraId?`, `provider?` (`local`/`online`/`cloud`) | `{cameraId, setups: [{setupId, name, description, cameraId, provider, isDefault, rank, revision, config}]}`; 400 bad provider, 503 no Mongo | **not read-only**: upserts the local catalog into Atlas `tryon_setups` on every call, then reads it back |
| POST | `/api/tryon/setups/{setupId}/use` | L+O | JSON `{cameraId}` | `{cameraId, setupId, updatedAt}`; 400 missing ids, 404 unknown setup, 503 no Mongo | Atlas upsert `tryon_setups` + `camera_setup_preferences` |
| POST | `/api/tryon/jobs/{job_id}/retry` | L+O | same as `/api/worker/jobs/{job_id}/retry` (alias, calls it directly) | same | same |

## Worker routes

| Method | Path | Auth | Request | Response | Side effects |
|---|---|---|---|---|---|
| GET | `/api/worker/status` | L+O | none | runtime status `{workerRunning, stopped, enabled, pollIntervalSeconds, currentJobId, lastClaimedJobId, lastLoopAt, lastHeartbeatAt, lastSuccessAt, lastFailureAt, lastFailureCode, lastFailureMessage, workerJobActive}` + `settings`, `recentEvents[20]`, `services`, `queueRoot`, `localApiUrl`, `queueCounts{queued,claimed,processing,uploading_result,notifying_camera,retry_wait,done,failed}`, `activeSetups`, `queueError?` | fs read `.runtime`; Atlas `tryon_jobs` counts + `tryon_setups` count |
| GET | `/api/worker/settings` | L+O | none | `{enabled, pollIntervalSeconds, updatedAt, updatedBy}` | fs read `.runtime` |
| POST | `/api/worker/settings` | L+O | JSON `WorkerSettingsRequest` `{enabled, pollIntervalSeconds, updatedBy?}` (interval must be one of the allowed values, else 60) | normalised settings `{enabled, pollIntervalSeconds, updatedAt, updatedBy}` | fs write `.runtime` settings; appends `worker_settings_updated` event |
| POST | `/api/worker/service-action` | **L+O+secret** | JSON `ServiceActionRequest` `{target, action, requestedBy?}`; actions `start`/`restart`/`run_now` | `{target, action, acceptedAt}` (launchctl acceptance, not completion); 400 unknown target/action, 401 secret, 409 job active | launchd control of the app/worker service; appends `service_action_requested` event |
| POST | `/api/worker/jobs/{job_id}/retry` | L+O | JSON `RetryWorkerJobRequest` `{target?="queued"|"retry_wait", delayMinutes?=0 (0-1440, retry_wait only), requestedBy?, resetAttempts?=false}` | `{jobId, previousStatus, status, stage, nextAttemptAt, retryScheduled, resetAttempts, requestedBy, updatedAt}`; 400 bad target/delay, 404 unknown job, 409 job active or non-retryable, 503 no Mongo | Atlas `tryon_jobs` update (status, stage, error cleared, lease/heartbeat cleared, attemptCount zeroed when `resetAttempts`); appends `job_retried` event |

## Capability & contract routes

| Method | Path | Auth | Request | Response | Side effects |
|---|---|---|---|---|---|
| GET | `/api/capabilities` | L+O | none | `{models_root, assets{}, features{}, feature_matrix{}, summary{ready,degraded,unavailable}, runtime, warnings[]}` (cached report) | none |
| GET | `/api/quality-contracts` | L+O | none | `QUALITY_CONTRACTS` dict keyed by feature | none |

## `local-ai` service routes (14) — one shared shape

Every `local-ai` route dispatches through `run_local_ai_service(_ROOT, <serviceId>, payload)`
and returns **the service runner's `result` dict as-is** (`JSONResponse(result)`); the
per-service result keys are documented in `docs/LOCAL_AI_SERVICES.md`. The request body is a
free-form JSON object passed straight to the runner (the dedicated routes take `dict`;
`/api/local-ai/jobs` wraps it as `{serviceId, payload}`). Each call also writes a job record
`{schemaVersion, jobId, serviceId, status: running|completed|failed, startedAt, updatedAt,
result?|error?}` under `.runtime/local_ai/`. Error mapping: `/api/local-ai/jobs` returns 404
for a missing file, 400 for `ValueError` (unknown service / bad payload), 500 otherwise; the
dedicated routes do not catch, so a bad payload surfaces as a 500. Side effects for all:
local model-pack execution and `.runtime/local_ai` writes; the two google-edge routes call
the configured external Google-edge provider. None touch Atlas.

| Method | Path | serviceId | Notes |
|---|---|---|---|
| GET | `/api/local-ai/services` | — | `service_registry(models_root)` → `{contractVersion, generatedAt, services: [{serviceId, label, milestone, version, active, zeroExternalCost, requiredModelPacks, missingModelPacks, status, inputSchema, …}]}` |
| GET | `/api/local-ai/model-packs` | — | `evaluate_model_packs(models_root)` → `{contractVersion, generatedAt, modelPacks: {<packId>: {packId, label, status, required[], optional[], zeroExternalCost}}}` |
| POST | `/api/local-ai/jobs` | body `serviceId` | generic dispatcher; request `LocalAiJobRequest` `{serviceId, payload={}}` |
| POST | `/api/local-ai/garments/isolate` | `garment_isolation` | |
| POST | `/api/local-ai/product-photo/cleanup` | `product_photo_cleanup` | |
| POST | `/api/local-ai/quality/brand-safety` | `brand_safety_analyzer` | |
| POST | `/api/local-ai/quality/tryon-gate` | `tryon_quality_gate` | |
| POST | `/api/local-ai/google-edge/analyze` | `google_edge_analyzer` | external Google-edge call |
| POST | `/api/local-ai/google-edge/tryon` | `google_edge_tryon` | external Google-edge call; the worker's google-edge render branch calls this |
| POST | `/api/local-ai/editing/inpaint` | `local_inpainting_cleanup` | |
| POST | `/api/local-ai/variants/generate` | `campaign_variant_generator` | |
| POST | `/api/local-ai/events/{event_id}/social-stills` | `event_social_still_builder` | path `event_id` is merged into the payload as `eventId` |
| GET | `/api/local-ai/reports` | `local_ai_service_reporting` | empty payload |
| GET | `/api/local-ai/reports/export` | — | `export_report_csv` → `{path}`; writes `.runtime/local_ai/reports/local_ai_services.csv` |

## Callers and deprecation candidates

Method: every route path was grepped in this repo (`scripts/`, `studio_tools/`, `services/`,
`tests/`, `docs/`, `README.md`, `HANDOVER.md`, `launchd/`, shell scripts) and in the camera
worktree (`lib/tryon/*`, `app/api/internal/tryon/*`, plus a repo-wide grep for `7860`,
`/api/tryon/`, `/api/worker/`, `/api/local-ai`). **camera never calls try-on over HTTP**: its
`/api/tryon/setups` and `/api/tryon/setups/[setupId]/use` are camera's own routes reading
Atlas through `lib/tryon/setup-resolution.ts`; the try-on integration is the shared
`tryon_jobs` collection plus the worker's outbound completion callback. Documentation-only
mentions (README route lists, `docs/LOCAL_AI_SERVICES.md`) do not count as callers.

Routes with a code caller (13):

| Route | Evidence |
|---|---|
| `GET /`, `/set-garment`, `/garments`, `/worker-control` | `studio_tools/templates/navbar.html:8-10`, `landing.html:29-39`, `library.html:31` |
| `POST /upload_garment` | `studio_tools/templates/index.html:140` |
| `POST /save_package` | `studio_tools/templates/index.html:305` |
| `POST /api/tryon/run` | `scripts/tryon_queue_worker.py:315` (`TRYON_LOCAL_API_URL` default), `scripts/ab_render_expose_arms.py:77` |
| `POST /api/tryon/jobs/{job_id}/retry` | `studio_tools/templates/worker_control.html:360` |
| `GET /api/worker/status` | `studio_tools/templates/worker_control.html:296` |
| `POST /api/worker/settings` | `studio_tools/templates/worker_control.html:334` |
| `POST /api/worker/service-action` | `studio_tools/templates/worker_control.html:311` |
| `GET /api/capabilities` | `scripts/service_healthcheck.py:23`, `scripts/tryon_queue_worker.py:2436-2455`, `scripts/verify_tryon_worker_setup.py:140` |
| `POST /api/local-ai/google-edge/tryon` | `scripts/tryon_queue_worker.py:1795,1799`; `tests/test_worker_google_edge.py:78` |

**Zero-caller routes (18) — deprecation candidates.** Nothing is deleted here; each needs an
owner decision.

| Route | Only references found |
|---|---|
| `GET /api/tryon/setups` | `README.md:352,754,785` (curl examples). camera reads `tryon_setups` from Atlas directly. |
| `POST /api/tryon/setups/{setupId}/use` | `README.md:763`. camera has its own `/api/tryon/setups/[setupId]/use` writing `camera_setup_preferences`. |
| `GET /api/worker/settings` | `README.md:50,813`. Worker Control reads settings from `/api/worker/status.settings`. |
| `POST /api/worker/jobs/{job_id}/retry` | `README.md:862` curl example; the UI uses the `/api/tryon/jobs/{id}/retry` alias. One of the two should go. |
| `GET /api/quality-contracts` | `README.md:37` |
| `GET /api/local-ai/services` | `README.md:38`, `docs/LOCAL_AI_SERVICES.md:92` |
| `GET /api/local-ai/model-packs` | `README.md:39`, `docs/LOCAL_AI_SERVICES.md:98` |
| `POST /api/local-ai/jobs` | `README.md:40`, `docs/LOCAL_AI_SERVICES.md:104` |
| `POST /api/local-ai/garments/isolate` | `README.md:41`, `docs/LOCAL_AI_SERVICES.md:121` |
| `POST /api/local-ai/product-photo/cleanup` | `README.md:42`, `docs/LOCAL_AI_SERVICES.md:122` |
| `POST /api/local-ai/quality/brand-safety` | `README.md:43`, `docs/LOCAL_AI_SERVICES.md:123` |
| `POST /api/local-ai/quality/tryon-gate` | `README.md:44`, `docs/LOCAL_AI_SERVICES.md:124` |
| `POST /api/local-ai/google-edge/analyze` | none at all (not even docs) |
| `POST /api/local-ai/editing/inpaint` | `README.md:45`, `docs/LOCAL_AI_SERVICES.md:125` |
| `POST /api/local-ai/variants/generate` | `README.md:46`, `docs/LOCAL_AI_SERVICES.md:126` |
| `POST /api/local-ai/events/{event_id}/social-stills` | `README.md:47`, `docs/LOCAL_AI_SERVICES.md:127` |
| `GET /api/local-ai/reports` | `README.md:48`, `docs/LOCAL_AI_SERVICES.md:128` |
| `GET /api/local-ai/reports/export` | `docs/LOCAL_AI_SERVICES.md:129` |

The 13 unused `local-ai` routes are one decision, not thirteen: the service family is reachable
through `/api/local-ai/jobs` alone, and `tests/test_local_ai_services.py` exercises the
runners, not the routes.

## Atlas collections touched (3)

- `tryon_jobs` — job queue state (worker/tryon retry routes, worker status counts)
- `tryon_setups` — setup catalog synced from the local file (setups list/use routes)
- `camera_setup_preferences` — per-camera selected setup (setups/use route)

Collection names are overridable via `TRYON_SETUP_COLLECTION` /
`TRYON_CAMERA_SETUP_PREFERENCE_COLLECTION`.
