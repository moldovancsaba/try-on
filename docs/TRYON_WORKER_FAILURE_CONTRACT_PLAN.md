# Try-On Worker Failure Contract Implementation Plan

## Scope

This plan covers local try-on service and queue worker changes required to support the Camera admin vetting workflow updates.

Camera owns admin-facing result state, archive buckets, rejection emails, and resubmission history. The local worker owns job execution and should notify Camera when a job completes successfully or reaches final failure.

## Goals

- Keep existing success completion behavior unchanged.
- Add a final-failure callback so Camera can archive failed jobs under `Failed Jobs`.
- Preserve resubmission metadata from Camera-created `tryon_jobs` documents.
- Make worker failure notifications idempotent and only send them after retries are exhausted.

## Configuration

Add a new optional environment variable:

```bash
CAMERA_TRYON_FAILED_URL=https://camera.messmass.com/api/internal/tryon/failed
```

Keep existing values:

```bash
CAMERA_TRYON_COMPLETE_URL=https://camera.messmass.com/api/internal/tryon/complete
CAMERA_TRYON_INTERNAL_SECRET=...
```

If `CAMERA_TRYON_FAILED_URL` is missing, the worker should continue existing local failure behavior and log that Camera failure archival notification is disabled.

## Worker Payload

On final failed outcome only, send:

```json
{
  "jobId": "job_...",
  "submissionId": "sub_...",
  "sourceSubmissionId": "sub_...",
  "cameraId": "camera_...",
  "leatherSuitId": "suit_...",
  "workerId": "mac-studio-01",
  "attemptCount": 3,
  "error": {
    "code": "processing_failed",
    "message": "Provider failed after final retry",
    "details": "..."
  },
  "processorMeta": {
    "pipelineVersion": "1.1.0",
    "processingProfile": "fal_tryon",
    "resolvedSetupId": "fal_ai_tryon",
    "resolvedSetupName": "Fal.ai Pro (FASHN)",
    "resolvedSetupRevision": "fal-fashn-tryon-v5",
    "setupSource": "job.assigned"
  },
  "resubmission": {
    "rootResultId": "result_...",
    "parentResultId": "result_...",
    "submissionAttempt": 2,
    "presetType": "f1_full_suit_repair"
  }
}
```

The `resubmission` object should be copied from optional fields Camera includes in the job `source` or `request` object.

## Worker Logic

Current behavior:

- Worker schedules `retry_wait` for transient failures.
- Worker marks final failures as `failed`.
- Worker only calls Camera on success via `CAMERA_TRYON_COMPLETE_URL`.

Required behavior:

- If `schedule_retry_or_failure(...)` returns `retry_wait`, do not notify Camera failed endpoint.
- If outcome is final `failed`, call `CAMERA_TRYON_FAILED_URL`.
- Include the current job snapshot and resolved setup metadata.
- If failure callback returns a transient HTTP status, record `processing.failureNotificationError` but do not requeue the try-on job automatically.
- If failure callback returns success, record `processing.failureNotifiedAt`.
- Make repeated calls safe by checking `processing.failureNotifiedAt`.

## Mongo Fields

Add worker-managed fields under `processing`:

```ts
processing.failureNotifiedAt?: string;
processing.failureNotificationError?: {
  code: string;
  message: string;
  occurredAt: string;
};
```

Preserve Camera-provided fields:

```ts
source.rootResultId?: string;
source.parentResultId?: string;
source.submissionAttempt?: number;
source.presetType?: string;
request.setupId?: string;
request.processingProfile?: string;
```

## API Verification

Extend `scripts/verify_tryon_worker_setup.py` to check:

- `CAMERA_TRYON_FAILED_URL` is present or explicitly marked optional.
- URL is reachable if present.
- Internal secret exists when either completion or failure callback is configured.

## Worker Events

Emit events:

- `camera_failure_notification_succeeded`
- `camera_failure_notification_failed`
- `camera_failure_notification_skipped`

Include:

- `jobId`
- final error code
- final status
- Camera response status

## Acceptance Criteria

- Successful jobs still call `/api/internal/tryon/complete`.
- Retry-wait jobs do not call `/api/internal/tryon/failed`.
- Final failed jobs call `/api/internal/tryon/failed`.
- Camera can archive failed jobs under `Failed Jobs`.
- Resubmission metadata survives from queued job to callback payload.
- Re-running notification for the same failed job is idempotent.
