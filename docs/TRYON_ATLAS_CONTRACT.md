# Try-On Atlas Contract

## Scope

This document defines the stable contract between Camera, MongoDB Atlas, and the local try-on worker.

Camera owns submission creation and suit catalog authoring. Atlas stores queue state and result metadata. The local worker owns processing, retries, publication, and completion callbacks.

## Schema versions

Current supported version:

- `tryon_jobs.schemaVersion = 1`
- `leather_suits.schemaVersion = 1`

Legacy rows without `schemaVersion` are accepted through a bounded compatibility path. The worker normalizes them to the current in-memory shape before validation.

Unsupported non-null schema versions fail fast with stable validation errors.

## tryon_jobs V1

Required fields:

```json
{
  "schemaVersion": 1,
  "jobId": "job_...",
  "status": "queued",
  "stage": "queued",
  "source": {
    "submissionId": "sub_...",
    "imageUrl": "https://...",
    "cameraId": "camera_..."
  },
  "request": {
    "leatherSuitId": "suit_..."
  },
  "processing": {
    "attemptCount": 0
  },
  "error": {}
}
```

Optional request fields:

- `request.setupId`
- `request.cameraId`
- `request.processingProfile`
- `request.garmentType` — `motorsport_suit | jersey | top | bottom`; a snapshot of the
  selected garment's type taken by Camera at job creation (camera#115). When present,
  the worker's render-category resolution uses it in place of the setup preset's
  `category` (try-on#37): `motorsport_suit → dresses/Full-Body/one-pieces`,
  `jersey/top → upper/tops`, `bottom → lower/bottoms`, mapped per provider. Absent
  (every job created before camera#115) ⇒ setup-driven resolution, exactly as before.
  An unrecognized value is logged and treated as absent — never a validation failure,
  so a newer Camera can ship a new type before this worker learns it.
  **Provider routing (2026-08-19):** a garment-typed `jersey`/`top`/`bottom` job whose
  resolved setup profile is `segmind_idm_vton` is rerouted to `fal_tryon` (FASHN v1.6)
  by the worker — verified side by side on live submissions, FASHN preserves garment
  lettering and the wearer's own lower body where IDM-VTON does not. `motorsport_suit`
  jobs and setups that explicitly choose the local or google-edge pipeline are never
  rerouted, and if fal is unconfigured the job falls back through the existing
  fal-fallback path. fal inputs travel inline as base64 data URIs (no ImgBB
  dependency).
- `request.sleeveStyle` — `sleeveless | short_sleeve | long_sleeve`; snapshot of the
  garment's sleeve style. Only consulted when `garmentType` is present and recognized.
  `short_sleeve` maps onto the local pipeline's `sleeve_length='short_sleeve'`,
  `long_sleeve` onto `'default'`. `sleeveless` on a `jersey`/`top` does NOT map onto
  `sleeve_length='sleeveless'` (whose historical semantics preserve already-bare
  arms by shrinking the edit mask) — it selects the local pipeline's
  `mask_mode='expose_arms'` instead (try-on#38), which keeps the arm regions inside
  the edit mask so the model synthesizes bare skin over any source-photo sleeves,
  with `sleeve_length` forced to `'default'` (the two are mutually exclusive).
- `request.outfitBottomLeatherSuitId` — presence marks the job as a **two-piece
  outfit** (try-on#39): `leatherSuitId` is the `top` piece, this field is the
  `bottom` piece. Additive: a job without it is a normal single-garment job,
  byte-identical behavior to before. Rules the worker enforces at claim time,
  each failing fast with a stable terminal error (category
  `invalid_job_contract`) before any render spend:
  - `outfit_top_type_mismatch` — `leatherSuitId`'s catalog `garmentType` must be `top`.
  - `outfit_bottom_type_mismatch` — this field's catalog `garmentType` must be
    `bottom` (also raised for a missing/inactive bottom, or the same id as the top).
  - `outfit_requires_local_provider` — outfit jobs run on the local provider only;
    Segmind/fal/Google-Edge profiles are rejected.
  Rendering is two sequential local passes — top (Upper) on the person photo,
  then bottom (Lower) on pass 1's output — with a **fixed top-first order**
  (the bottom's Lower mask cannot damage the rendered top; the reverse order
  would let an Upper mask repaint the waistband region). One job, one result:
  the intermediate pass-1 image never reaches ImgBB, the completion callback,
  or any Atlas field, and is deleted on every exit path; any pass failing
  retries the whole job from pass 1. Camera-side requirement: the request
  dedup hash MUST incorporate this field when present, or a top-only job and
  a top+bottom job for the same submission would collide (implemented in
  camera#116). Consumers keyed on `request.leatherSuitId` (usage counts,
  admin queue views) see the top piece only — a documented tradeoff.

Optional result fields:

- `result.publicResultUrl`
- `result.deleteUrl`
- `result.imgbbDeleteUrl` legacy alias for `result.deleteUrl`
- `result.provider`
- `result.uploadedAt`
- `processing.cameraNotifiedAt`
- `processing.publicationState`

Publication states:

- `not_started`: result publication has not completed any durable side effect.
- `uploaded`: media upload succeeded and the public URL is stored; retry must not upload again.
- `camera_notified`: Camera completion callback succeeded or was already accepted idempotently.

## leather_suits V1

Required fields:

```json
{
  "schemaVersion": 1,
  "leatherSuitId": "suit_...",
  "active": true,
  "sourceImageUrl": "https://...",
  "updatedAt": "2026-06-06T00:00:00Z"
}
```

Optional fields (Camera-authored, additive as of camera#115 — no schemaVersion bump):

- `garmentType` — `motorsport_suit | jersey | top | bottom`. Absent ⇒ the record
  predates the field, and every such record is in practice a motorsport suit (the
  only product this system ever had before garment types); consumers must treat
  absent as `motorsport_suit`.
- `sleeveStyle` — `sleeveless | short_sleeve | long_sleeve | null`; only meaningful
  for `jersey`/`top`.

Asset resolution order:

1. `sourceImageUrl`
2. `imageUrl`
3. `previewUrl`
4. `assetRelativePath`
5. `assetKey`

## Job statuses

Supported statuses:

- `queued`: waiting to be claimed.
- `claimed`: leased by a worker but not yet processing.
- `processing`: input download, suit resolution, or generation is running.
- `uploading_result`: result publication is running or already uploaded.
- `notifying_camera`: Camera completion callback is running or pending retry.
- `retry_wait`: transient failure waiting for another attempt.
- `done`: terminal success.
- `failed`: terminal or operator-retryable failure.

Active statuses:

- `claimed`
- `processing`
- `uploading_result`
- `notifying_camera`

Retryable statuses:

- `queued`
- `retry_wait`
- `failed`

Terminal statuses:

- `done`
- `failed`

## Job stages

Supported stages:

- `queued`
- `claimed`
- `downloading_input`
- `resolving_suit`
- `normalizing_job`
- `running_tryon`
- `uploading_result`
- `uploaded_result`
- `notifying_camera`
- `done`
- `failed`
- `aborted`
- `retry_requested`

Stages are diagnostic detail inside a status. Status drives claimability and retry behavior.

## Legacy compatibility

The worker normalizes legacy rows before validation:

- missing `schemaVersion` becomes `1`
- missing `status` becomes `queued`
- missing `stage` becomes the normalized status
- top-level `submissionId`, `sourceSubmissionId`, or `cameraSubmissionId` become `source.submissionId`
- top-level `imageUrl`, `sourceImageUrl`, or `photoUrl` become `source.imageUrl`
- top-level `leatherSuitId` or `suitId` become `request.leatherSuitId`
- top-level `cameraId` becomes `source.cameraId` and `request.cameraId`
- missing `processing.attemptCount` becomes `0`

Normalization does not hide invalid data. Missing required fields still fail validation and the job is marked failed or retryable according to the worker failure policy.

## Validation errors

Stable worker validation errors include:

- `unsupported_job_schema_version`
- `missing_job_id`
- `invalid_job_status`
- `invalid_job_stage`
- `missing_source_image_url`
- `missing_submission_id`
- `missing_leather_suit_id`
- `invalid_setup_id`
- `invalid_camera_id`
- `invalid_processing_profile`
- `invalid_attempt_count`
- `unsupported_suit_schema_version`
- `inactive_suit`
- `missing_suit_asset_reference`

## Maintenance rule

Any Camera or worker change that adds queue fields, status values, stage values, result publication fields, or suit asset fields must update this contract first.

## Idempotent publication rule

Publication must be safe across worker retries and restarts:

- If `result.publicResultUrl` exists and `processing.cameraNotifiedAt` is missing, the worker must skip media upload and retry only the Camera callback.
- If `processing.cameraNotifiedAt` exists, the worker must mark the job `done` without repeating upload or callback.
- Camera completion must be idempotent by `jobId`; duplicate callbacks for an already-materialized result should return success.
- Publication errors are stored under `processing.publicationError` and redacted in worker events.

## Critical infrastructure contract v2026.06

Current infrastructure contract:

- `infraContractVersion = 2026.06-critical-infra-v1`
- worker API contract: `tryon-api-v1`
- worker pipeline version: `1.1.0`
- provider metrics schema: `1`
- reconciliation report schema: `1`
- canary report schema: `1`

Additional worker heartbeat/status fields:

- `infraContractVersion`
- `apiContractVersion`
- `pipelineVersion`
- `maxConcurrency`
- `activeWorkerSlots`
- `queueSummary`
- `backpressure`
- `providerScorecard`

`queueSummary.backpressure` is advisory by default. It tells Camera/admin surfaces that the worker is overloaded, but the local worker continues draining existing jobs unless an operator disables it.

## Failure taxonomy

Final failed jobs must include:

```json
{
  "error": {
    "code": "timeout_retry_limit_reached",
    "message": "Timeout failed twice; job left failed behind the queue.",
    "details": "...",
    "category": "timeout",
    "operatorNote": {
      "category": "timeout",
      "label": "Provider or network timeout",
      "recommendedAction": "Retry once; after repeated timeout leave failed and review provider latency.",
      "message": "redacted short message"
    }
  }
}
```

Supported categories:

- `timeout`
- `provider_error`
- `validation_error`
- `upload_error`
- `callback_error`
- `operator_cancel`
- `local_runtime_error`
- `unknown`

## Provider scorecard and circuit-breaker state

Provider metrics are stored locally in `.runtime/provider_metrics.json` and are also exposed through worker status.

Tracked providers:

- `local`
- `segmind`
- `fal`
- `imgbb`
- `camera`

Each provider records:

- request count
- success count
- failure count
- timeout count
- slow count
- consecutive failures
- circuit open timestamp
- recent latency samples
- daily request counters

Circuit policy:

- a provider opens after `TRYON_PROVIDER_FAILURE_THRESHOLD` consecutive failures
- open circuits block new calls for `TRYON_PROVIDER_COOLDOWN_SECONDS`
- success resets consecutive failure count and closes the circuit
- daily limits block provider calls before external rate/cost collapse

## Reconciliation contract

`./.venv311/bin/python scripts/tryon_infra_cli.py reconcile` emits:

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-06-06T00:00:00Z",
  "findingCount": 0,
  "findings": []
}
```

Finding types:

- `done_missing_public_url`
- `uploaded_missing_camera_callback`
- `active_expired_lease`
- `failed_missing_category`

`safeReplay=true` means the condition is normally safe for an idempotent replay path. Unsafe findings require manual review.
