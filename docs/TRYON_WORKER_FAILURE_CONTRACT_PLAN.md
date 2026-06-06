# Try-On Worker Failure Contract Implementation Plan

## Scope

This plan covers the local try-on service and queue worker changes required to support Camera admin failure archival.

Camera owns admin-facing result state, archive buckets, rejection emails, and resubmission history. The local worker owns job execution and should notify Camera when a job completes successfully or reaches final failure.

## Goals

- Keep existing success completion behavior unchanged.
- Add a final-failure callback so Camera can archive failed jobs under `Failed Jobs`.
- Preserve resubmission metadata from Camera-created queue documents.
- Make worker failure notifications idempotent and only send them after retries are exhausted.

## Configuration

Use environment variables for all Camera callback URLs and shared secrets. Do not commit real URLs, tokens, keys, credentials, or environment-specific hostnames.

Required categories:

- Success callback URL.
- Optional final-failure callback URL.
- Shared internal callback secret.
- Queue/database connection settings.
- Media upload settings.

If the final-failure callback URL is missing, the worker should continue existing local failure behavior and log that Camera failure archival notification is disabled.

## Worker Payload

On final failed outcome only, send a compact job snapshot containing:

- Job identifier.
- Source submission identifier.
- Camera/event context when present.
- Garment/catalog identifier.
- Worker identifier.
- Attempt count.
- Structured final error code/message/details.
- Resolved setup metadata without provider secrets.
- Resubmission chain metadata when present.

The resubmission object should be copied from optional fields Camera includes in the queue document.

## Worker Logic

Current behavior:

- Worker schedules retry-wait for transient failures.
- Worker marks final failures as failed.
- Worker calls Camera on success.

Required behavior:

- Retry-wait jobs do not notify the final-failure callback.
- Final failed jobs notify Camera if the failure callback is configured.
- Failure notification includes current job snapshot and resolved setup metadata.
- Transient callback failure records a notification error but does not automatically requeue the try-on job.
- Successful notification records a notification timestamp.
- Repeated calls are safe by checking the notification timestamp.

## Mongo Fields

Worker-managed fields under `processing`:

```ts
processing.failureNotifiedAt?: string;
processing.failureNotificationError?: {
  code: string;
  message: string;
  occurredAt: string;
};
```

Preserve Camera-provided resubmission fields under source/request metadata.

## API Verification

Extend the worker setup verification script to check:

- Failure callback URL is present or explicitly marked optional.
- Callback URL is reachable if present.
- Internal secret exists when either completion or failure callback is configured.
- No secret values are printed to stdout.

## Worker Events

Emit operational events for success, failure, and skipped failure notifications. Include identifiers and response status, but never include secrets, API keys, signed URLs, or raw credentials.

## Acceptance Criteria

- Successful jobs still call the success callback.
- Retry-wait jobs do not call the final-failure callback.
- Final failed jobs call the final-failure callback when configured.
- Camera can archive failed jobs under `Failed Jobs`.
- Resubmission metadata survives from queued job to callback payload.
- Re-running notification for the same failed job is idempotent.
