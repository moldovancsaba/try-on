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
