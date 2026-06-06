# Try-On Vetting Workflow Implementation Plan

## Scope

This plan covers the Camera admin application changes for try-on result vetting, resubmission history, rejection email handling, and failed-job archival.

The local try-on worker remains responsible for processing `tryon_jobs` and notifying Camera on completion or final failure. Camera remains the source of truth for admin-facing result state.

## Goals

- When an admin resubmits a try-on result, move the original result out of active vetting into `Archived Rejected`.
- Show the full submission/resubmission history on resubmitted try-on results.
- Let admins choose a third preset when resubmitting.
- Add a rejection email checkbox and editable message for manually rejected images.
- Automatically archive final failed jobs under `Failed Jobs` instead of leaving them in active vetting.

## Data Model

Add or extend the admin-facing try-on result document with these fields:

```ts
status:
  | "pending_vetting"
  | "approved"
  | "archived_rejected"
  | "resubmitted"
  | "failed_archived";

archiveBucket?: "archived_rejected" | "failed_jobs" | null;

jobId: string;
sourceSubmissionId: string;
rootResultId?: string;
parentResultId?: string;
resubmittedFromResultId?: string;
resubmittedToJobId?: string;
resubmittedToResultId?: string;
submissionAttempt: number;

presetType?: "same" | "logo_safe" | "f1_full_suit_repair";
setupId?: string;
processingProfile?: string;

rejection?: {
  reason?: string;
  message?: string;
  emailUser: boolean;
  emailedAt?: string;
  rejectedBy: string;
  rejectedAt: string;
};

failure?: {
  code?: string;
  message?: string;
  details?: string;
  failedAt: string;
};
```

Add a timeline/event collection if one does not already exist:

```ts
tryon_result_events {
  id: string;
  resultId: string;
  rootResultId: string;
  type:
    | "created"
    | "approved"
    | "manual_rejected"
    | "resubmit_requested"
    | "archived_rejected"
    | "job_enqueued"
    | "job_completed"
    | "job_failed"
    | "failed_archived"
    | "email_sent";
  actorId?: string;
  createdAt: string;
  payload: Record<string, unknown>;
}
```

Indexes:

- `status`
- `archiveBucket`
- `jobId`
- `sourceSubmissionId`
- `rootResultId`
- `parentResultId`

## Admin API

### Resubmit Result

```http
POST /admin/api/tryon-results/:resultId/resubmit
```

Payload:

```json
{
  "presetType": "f1_full_suit_repair",
  "setupId": "fal_ai_tryon",
  "reason": "Improve logo and text fidelity"
}
```

Behavior:

- Validate the source result is in `pending_vetting` or another resubmittable state.
- Update the original result:
  - `status = "resubmitted"`
  - `archiveBucket = "archived_rejected"`
  - `resubmittedToJobId = <new job id>`
- Insert `archived_rejected` and `resubmit_requested` events.
- Create a new `tryon_jobs` record with:
  - same source image/submission reference
  - selected `setupId` or `processingProfile`
  - `source.parentResultId`
  - `source.rootResultId`
  - incremented `source.submissionAttempt`
- Return the new job id and the archived original result id.

### Result History

```http
GET /admin/api/tryon-results/:resultId/history
```

Behavior:

- Resolve `rootResultId`.
- Return every attempt in chronological order.
- Include thumbnail/result URL, preset, provider, job id, status, rejection reason, failure reason, and admin events.

### Manual Reject

```http
POST /admin/api/tryon-results/:resultId/reject
```

Payload:

```json
{
  "reason": "logo_distorted",
  "message": "We could not approve this image because the suit branding was distorted.",
  "emailUser": true
}
```

Behavior:

- Update result:
  - `status = "archived_rejected"`
  - `archiveBucket = "archived_rejected"`
  - persist `rejection`
- If `emailUser` is true, send the editable rejection message to the user.
- Record `manual_rejected` and optional `email_sent` events.
- Do not send rejection email for resubmission flow unless an admin explicitly performs manual rejection.

### Worker Failure Callback

```http
POST /api/internal/tryon/failed
```

Payload from worker:

```json
{
  "jobId": "job_...",
  "submissionId": "sub_...",
  "sourceSubmissionId": "sub_...",
  "cameraId": "camera_...",
  "leatherSuitId": "suit_...",
  "workerId": "mac-studio-01",
  "error": {
    "code": "processing_failed",
    "message": "Provider failed after final retry",
    "details": "..."
  },
  "processorMeta": {
    "processingProfile": "fal_tryon",
    "resolvedSetupId": "fal_ai_tryon",
    "resolvedSetupRevision": "fal-fashn-tryon-v5"
  }
}
```

Behavior:

- Authenticate with existing internal try-on secret.
- Upsert an admin result for the job.
- Set:
  - `status = "failed_archived"`
  - `archiveBucket = "failed_jobs"`
- Record `job_failed` and `failed_archived` events.
- Do not show this item in active vetting.

## Admin UI

### Active Vetting

Add actions per result:

- Approve
- Reject
- Resubmit

Reject dialog:

- Reason selector
- Editable email message
- `Email user` checkbox
- Submit button

Resubmit dialog:

- Preset selector:
  - `Same preset`
  - `Logo/Text Safe`
  - `F1 Full Suit Repair`
- Show provider/setup description for each preset.
- Confirm action archives the current result and enqueues a new job.

### Result Detail

Add `Submission History` panel:

- Attempt number
- Thumbnail/result URL
- Preset/setup/provider
- Job status
- Admin action
- Rejection/failure reason
- Timestamp

### Archive Views

Add or update tabs/filters:

- `Active Vetting`
- `Archived Rejected`
- `Failed Jobs`

Filtering:

- Active vetting: `status = "pending_vetting"`
- Archived rejected: `archiveBucket = "archived_rejected"`
- Failed jobs: `archiveBucket = "failed_jobs"`

## Preset Mapping

Recommended mapping for the third preset:

```ts
{
  presetType: "f1_full_suit_repair",
  setupId: "fal_ai_tryon",
  processingProfile: "fal_tryon",
  label: "F1 Full Suit Repair"
}
```

Fallback should remain worker-controlled. If FAL fails, the local worker can route to Segmind/local based on its own provider fallback rules.

## Migration

- Backfill rejected results to `archiveBucket = "archived_rejected"`.
- Backfill final failed job records to `status = "failed_archived"` and `archiveBucket = "failed_jobs"`.
- Build attempt chains by grouping existing records by `sourceSubmissionId` and creation time.
- Assign `rootResultId` to the earliest known result in each chain.
- Set `submissionAttempt` incrementally.

## Acceptance Criteria

- Resubmitting a result removes it from active vetting immediately.
- The original resubmitted result appears in `Archived Rejected`.
- The newly generated result appears in active vetting only after worker completion.
- Resubmitted result detail shows all previous attempts.
- Admin can choose `F1 Full Suit Repair` as the third preset.
- Manual rejection can send an email when `Email user` is checked.
- Manual rejection does not send email when unchecked.
- Final failed jobs appear in `Failed Jobs`.
- Retry-wait jobs do not appear in `Failed Jobs` until retries are exhausted.
