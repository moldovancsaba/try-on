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
- `result.provider`
- `result.uploadedAt`
- `processing.cameraNotifiedAt`
- `processing.publicationState`

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
