# Try-On Critical Infrastructure LLD and Operations Guide

## Scope

This document covers the production reliability layer for the local try-on worker, provider calls, queue health, diagnostics, canary checks, reconciliation, throughput planning, and incident recovery.

Implemented board issues:

- #15 Provider SLA routing, timeout policy, and circuit breaker
- #16 Queue concurrency controller with safe parallelism limits
- #17 Camera backpressure when try-on queue is overloaded
- #18 Provider performance scorecard with latency and failure tracking
- #19 Replay and reconciliation audit across Atlas, Camera, and image hosting
- #20 Synthetic canary job and alerting for worker/provider health
- #21 Failed-job taxonomy with automatic operator notes
- #22 Load test plan and throughput benchmark for queue processing
- #23 Disaster recovery runbook for worker, database, and callbacks
- #24 Cost and rate-limit controls per provider

## Versioning

- Infrastructure contract: `2026.06-critical-infra-v1`
- Try-on API contract: `tryon-api-v1`
- Worker pipeline: `1.1.0`
- Provider metrics schema: `1`
- Reconciliation schema: `1`
- Canary schema: `1`

Version changes are required when the worker status payload, queue status semantics, provider metric schema, reconciliation finding shape, or failure taxonomy changes.

## Low-level design

### Worker loop

1. Load env and `.config/worker_settings.json`.
2. Refresh provider circuit-breaker policies.
3. Summarize queue depth and age.
4. Publish `queueSummary`, `backpressure`, and `providerScorecard` into local status and Atlas heartbeat.
5. Recover interrupted/stale jobs.
6. Claim one eligible job by durable lease.
7. Run provider path through the circuit-breaker wrapper.
8. Store provider metrics, publication state, callback state, and failure taxonomy.

### Provider control

The worker wraps all external provider-like calls:

- local try-on API
- online try-on provider route
- optional provider route
- ImgBB upload
- Camera completion callback

Each call records latency and success/failure. Repeated failures open a circuit, blocking future calls to that provider until cooldown expires. This prevents the queue from repeatedly spending minutes on a provider that is currently unhealthy.

### Concurrency controller

`TRYON_MAX_CONCURRENCY` is part of the contract and status payload. The current live worker remains single-slot by default and by implementation safety. This is intentional because the local try-on API is single-task and external provider traffic needs measured limits first.

Do not raise concurrency above `1` for production until a separate implementation proves duplicate-claim safety, local API parallel safety, and provider rate-limit safety under load.

### Backpressure

Backpressure is computed from:

- ready queue depth
- oldest ready job age

The worker does not discard or pause existing jobs when pressure is active. It publishes pressure status so Camera/admin can slow or stop new intake.

### Reconciliation

The reconciliation CLI audits for mismatches:

- `done` without public result URL
- uploaded result without Camera callback
- active job with expired lease
- failed job without taxonomy category

Safe replay cases are marked with `safeReplay=true`.

### Failure taxonomy

Every final failure should have:

- `error.code`
- `error.message`
- `error.details`
- `error.category`
- `error.operatorNote`

Categories are stable and documented in `docs/TRYON_ATLAS_CONTRACT.md`.

## CLI guide

### Full infrastructure status

```bash
./.venv311/bin/python scripts/tryon_infra_cli.py status
```

Returns queue counts, backpressure state, provider scorecard, and recent event metrics.

Exit codes:

- `0`: healthy/no queue pressure
- `2`: queue pressure active
- `1`: hard CLI/config failure

### Reconciliation audit

```bash
./.venv311/bin/python scripts/tryon_infra_cli.py reconcile --limit 200
```

Exit codes:

- `0`: no findings
- `2`: findings exist
- `1`: hard CLI/config failure

### Backfill failure taxonomy

```bash
./.venv311/bin/python scripts/tryon_infra_cli.py backfill-failure-notes --limit 500
```

Use after deploying this contract to annotate older failed jobs.

### Canary

```bash
./.venv311/bin/python scripts/tryon_canary.py
```

Writes `.runtime/canary_status.json` and validates local app/worker service health.

### Load benchmark plan

```bash
./.venv311/bin/python scripts/tryon_load_benchmark.py --jobs 20 --median-seconds 180
```

Writes `.runtime/load_benchmark_plan.json`. This is a safe dry-run capacity projection, not a customer-data load generator.

## Environment variables

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

## Operator user guide

Daily check:

1. Run `scripts/tryon_infra_cli.py status`.
2. Confirm `backpressure.active=false`.
3. Confirm no provider has `circuitOpenUntil` in the future.
4. Confirm `failureCategories.timeout` and `provider_error` are not increasing quickly.
5. Run `scripts/tryon_canary.py` before important event windows.

If queue is growing:

1. Check `readyCount` and `oldestReadyAgeSeconds`.
2. Check provider scorecard for high latency or open circuits.
3. If one provider is unhealthy, keep the circuit closed/open according to policy and let fallback process if configured.
4. If all providers are unhealthy, stop new Camera intake using Camera-side controls and keep existing jobs draining.

If jobs failed:

1. Run `scripts/tryon_infra_cli.py reconcile`.
2. Review `error.category` and `error.operatorNote`.
3. Replay only records marked safe or known idempotent.
4. Do not manually edit Atlas result URLs unless reconciliation shows the exact mismatch.

## Disaster recovery runbook

### Worker offline

```bash
launchctl print gui/$(id -u)/com.tryon.camera-worker
launchctl kickstart -k gui/$(id -u)/com.tryon.camera-worker
./.venv311/bin/python scripts/service_healthcheck.py
```

Expected recovery: stale leases are returned to retry/queue by the worker startup recovery path.

### App offline

```bash
launchctl print gui/$(id -u)/com.tryon.app-server
launchctl kickstart -k gui/$(id -u)/com.tryon.app-server
./.venv311/bin/python scripts/service_healthcheck.py
```

The worker checks local API readiness before local jobs. Provider-based jobs can still be blocked by their own circuit state.

### Provider outage

1. Run `scripts/tryon_infra_cli.py status`.
2. Confirm circuit state and latency/failure counts.
3. Let circuit cooldown protect the queue.
4. Do not increase timeout blindly. Raise timeout only if provider latency is healthy but consistently above configured threshold.

### Callback mismatch

1. Run `scripts/tryon_infra_cli.py reconcile`.
2. Look for `uploaded_missing_camera_callback`.
3. Re-run worker for eligible rows or use the established callback replay path.
4. Confirm Camera result appears in vetting.

### Database inconsistency

1. Export the reconciliation report.
2. Do not bulk-edit Atlas manually.
3. Repair one finding type at a time.
4. Run reconciliation again after each repair batch.

## Definition of Done checklist

- Code path is versioned and documented.
- Worker status exposes operator-visible health.
- Failure modes produce stable categories and notes.
- Provider calls are bounded by timeout, circuit breaker, and daily limits.
- Queue pressure is measurable.
- Reconciliation identifies safe/unsafe repair paths.
- Canary and benchmark CLIs exist.
- Tests cover core policy behavior without live provider calls.
- README and API/Atlas contract are updated.
