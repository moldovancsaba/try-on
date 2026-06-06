# Try-On Vetting Workflow Implementation Plan

## Scope

This plan covers Camera admin application changes for try-on result vetting, resubmission history, rejection email handling, and failed-job archival.

The local try-on worker remains responsible for processing queue jobs and notifying Camera on completion or final failure. Camera remains the source of truth for admin-facing result state.

## Goals

- When an admin resubmits a try-on result, move the original result out of active vetting into Archived Rejected.
- Show submission/resubmission history on resubmitted try-on results.
- Let admins choose an alternate approved preset when resubmitting.
- Add rejection email controls for manually rejected images.
- Automatically archive final failed jobs under Failed Jobs instead of leaving them in active vetting.

## Data Model

Admin-facing result state should include:

- Review status.
- Archive bucket.
- Job/source identifiers.
- Resubmission chain identifiers.
- Attempt number.
- Selected setup or processing profile.
- Optional rejection details.
- Optional failure details.

Add or reuse an event/timeline collection for moderation history when detailed audit history is required.

Recommended indexes:

- Status.
- Archive bucket.
- Job identifier.
- Source submission identifier.
- Resubmission root/parent identifiers.

## Admin API

### Resubmit Result

Behavior:

- Validate the source result is resubmittable.
- Archive the original result as rejected/resubmitted.
- Record resubmission events.
- Create a new queue job using the original source image/submission reference.
- Store selected setup metadata and resubmission chain metadata.
- Return the new job identifier and archived original result identifier.

### Result History

Behavior:

- Resolve the root result.
- Return every attempt in chronological order.
- Include result URL, selected setup, job status, moderation status, failure/rejection reason, and timestamps.

### Manual Reject

Behavior:

- Archive the result as rejected.
- Persist rejection reason/message/email preference.
- Send the editable rejection message only when explicitly requested.
- Do not send rejection email for resubmission flow unless an admin explicitly performs manual rejection.

### Worker Failure Callback

Behavior:

- Authenticate with the existing internal worker secret.
- Upsert an admin-facing failed result for the job.
- Archive it under Failed Jobs.
- Record failure events.
- Do not show the item in active vetting.

## Admin UI

### Active Vetting

Actions per result:

- Approve.
- Reject.
- Resubmit.
- Mark or remove Great when applicable.

Reject dialog:

- Reason selector.
- Editable email message.
- Email user checkbox.
- Submit button.

Resubmit dialog:

- Setup/preset selector.
- Setup description.
- Confirmation that the current result will leave active vetting and a new job will be queued.

### Result Detail

Show submission history:

- Attempt number.
- Thumbnail/result URL.
- Setup/preset.
- Job status.
- Admin action.
- Rejection/failure reason.
- Timestamp.

### Archive Views

Maintain separate views for:

- Active Vetting.
- Approved.
- Rejected.
- Greatest Hits.
- Failed Jobs.

## Preset Mapping

Preset labels and provider routing should remain configurable through setup records and worker configuration. Avoid documenting provider-specific model identifiers or internal fallback details in public repository docs.

## Migration

- Backfill rejected results into the rejected archive bucket.
- Backfill final failed job records into Failed Jobs.
- Build attempt chains by grouping existing records by source submission and creation time.
- Assign root result identifiers to the earliest known result in each chain.
- Set attempt numbers incrementally.

## Acceptance Criteria

- Resubmitting removes a result from active vetting immediately.
- The original resubmitted result appears in Rejected archive.
- The newly generated result appears in active vetting only after worker completion.
- Resubmitted result detail shows prior attempts.
- Manual rejection can send an email when Email user is checked.
- Manual rejection does not send email when unchecked.
- Final failed jobs appear in Failed Jobs.
- Retry-wait jobs do not appear in Failed Jobs until retries are exhausted.
