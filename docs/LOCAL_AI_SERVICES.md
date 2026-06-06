# Local AI Services LLD

## Scope

This document defines the `Local AI Services - Zero External Cost v1` delivery lane.

The goal is to create high-value image services on the existing try-on stack without paid external inference or API costs in the first version.

Implemented service contracts:

- `garment_isolation`
- `product_photo_cleanup`
- `brand_safety_analyzer`
- `tryon_quality_gate`
- `local_inpainting_cleanup`
- `campaign_variant_generator`
- `event_social_still_builder`
- `synthetic_fixture_generator`
- `local_ai_service_reporting`

## Design System Boundary

All UI/UX/frontend implementation for this lane must use only the Sovereign Squad General Design System and must follow `sovereignsquad/general-design-system#81`.

The first implementation exposes APIs and CLI surfaces. Any admin console or Camera-facing UI that consumes these APIs must use GDS primitives, GDS tokens, semantic controls, visible focus, screen-reader labels, contrast-safe states, and reduced-motion-safe polling.

## Architecture

```text
Local input image / approved artifact
  -> Local AI service registry
  -> Model pack readiness check
  -> Typed service job
  -> Deterministic local image pipeline
  -> Artifact directory
  -> JSON sidecar metadata
  -> Job record
  -> API / CLI / future GDS operator console
```

The implementation lives in `services/local_ai_services.py`.

Runtime state is stored under:

```text
.runtime/local_ai/
  artifacts/
  jobs/
  reports/
```

## Contracts

Current contract version:

```text
local-ai-services-v1
```

Job schema version:

```text
1
```

Artifact schema version:

```text
1
```

Example service descriptor:

```json
{
  "serviceId": "product_photo_cleanup",
  "version": 1,
  "active": true,
  "zeroExternalCost": true,
  "requiredModelPacks": ["pillow_core"],
  "status": "ready",
  "inputSchema": "ProductCleanupJob",
  "outputSchema": "LocalAiArtifactSet"
}
```

## APIs

List services:

```http
GET /api/local-ai/services
```

Verify model packs:

```http
GET /api/local-ai/model-packs
```

Run generic job:

```http
POST /api/local-ai/jobs
```

```json
{
  "serviceId": "product_photo_cleanup",
  "payload": {
    "inputImagePath": "/absolute/path/input.png",
    "outputRatios": ["1:1", "4:5"],
    "backgroundMode": "white"
  }
}
```

Convenience endpoints:

```text
POST /api/local-ai/garments/isolate
POST /api/local-ai/product-photo/cleanup
POST /api/local-ai/quality/brand-safety
POST /api/local-ai/quality/tryon-gate
POST /api/local-ai/editing/inpaint
POST /api/local-ai/variants/generate
POST /api/local-ai/events/{eventId}/social-stills
GET  /api/local-ai/reports
GET  /api/local-ai/reports/export
```

## CLI

List services:

```bash
./.venv311/bin/python scripts/local_ai_services.py list
```

Verify model packs:

```bash
./.venv311/bin/python scripts/local_ai_services.py model-packs
```

Run a service:

```bash
./.venv311/bin/python scripts/local_ai_services.py run product_photo_cleanup --payload payload.json
```

Generate synthetic fixtures:

```bash
./.venv311/bin/python scripts/local_ai_services.py fixtures
```

Export report:

```bash
./.venv311/bin/python scripts/local_ai_services.py report
```

## Runtime Flow

1. Caller lists services and confirms readiness.
2. Caller submits a typed payload through API or CLI.
3. The service layer writes a running job record.
4. The local image operation runs without external AI/API calls.
5. Artifacts and sidecar metadata are written under `.runtime/local_ai/artifacts`.
6. The job record is marked completed or failed.
7. Reports aggregate local job records.

## Service Notes

`garment_isolation` uses a deterministic local mask heuristic and writes `garment.png` plus `mask.png`.

`product_photo_cleanup` normalizes background, contrast, and ratios for catalog-ready outputs.

`brand_safety_analyzer` compares source and output visual similarity and brightness drift to produce `pass`, `warn`, or `fail`.

`tryon_quality_gate` combines image integrity and optional brand safety into a review recommendation.

`local_inpainting_cleanup` performs local mask-based repair using bounded deterministic blur/composite behavior in v1.

`campaign_variant_generator` creates ratio-based local variants.

`event_social_still_builder` renders event-ready still assets from local images.

`synthetic_fixture_generator` creates private non-customer fixtures for regression tests.

`local_ai_service_reporting` aggregates local job records and estimates avoided external cost.

## Observability

Each job record includes:

- `jobId`
- `serviceId`
- `status`
- `startedAt`
- `updatedAt`
- `result` or `error`

Artifact metadata includes:

- `schemaVersion`
- `serviceId`
- `jobId`
- source/parent path where relevant
- image metrics

## Retries And Recovery

All service runners are idempotent by `jobId` and write into service/job-specific directories.

Failed jobs can be rerun with the same payload and same `jobId`; successful artifacts are overwritten deterministically.

Rollback is service-level:

- disable the service from the registry
- hide API/UI affordances
- leave existing artifacts and job records intact

## Security And Privacy

The v1 implementation performs local image operations only.

Do not add paid external inference/API calls to this lane without updating:

- this document
- `README.md`
- issue acceptance criteria
- runtime configuration docs
- privacy/security notes

API responses must not expose secrets or signed provider URLs. Public publication remains governed by the existing Camera/try-on publication path.

## Testing

Primary test file:

```bash
./.venv311/bin/python -m unittest tests.test_local_ai_services
```

The tests cover:

- service registry
- model pack readiness
- garment isolation artifacts
- brand safety and quality gate status
- synthetic fixture generation
- report export
- unsupported service rejection

