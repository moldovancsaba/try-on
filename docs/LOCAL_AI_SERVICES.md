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

## GitHub Project Board Handover

Implementation status:

- Code, APIs, CLI, documentation, user guide, release notes, README updates, and focused validation shipped in commit `edd0bc2`.
- Project-board handover documentation shipped in commit `18af984`.
- GitHub issues `#25` through `#36` were commented with delivery notes and closed as completed.
- Labels and milestones were created directly in the repository.
- Native GitHub Projects v2 card/status mutation is still pending because the account's GraphQL quota was exhausted during delivery.

Project board:

```text
https://github.com/users/moldovancsaba/projects/41
```

Pending board action after GraphQL quota reset:

1. Ensure issues `#25` through `#36` are present on `{try-on} - From IDEA to LIVE`.
2. Move all `#25` through `#36` project items to `Done`.
3. Add or confirm the board readme section `Current Next Pack: Local AI Services - Zero External Cost v1`.
4. Preserve this execution order on the board:

```text
1. #25 Platform: Local AI service registry - zero-cost capability contract
2. #26 Models: Local AI model pack governance - inventory, health, and rollout contract
3. #27 Studio: Garment isolation - reusable transparent asset pipeline
4. #28 Studio: Product photo cleanup - local catalog image workflow
5. #29 Quality: Brand safety analyzer - logo and text preservation scoring
6. #30 Quality: Try-on auto-review gate - local pass, warn, and rerun recommendation flow
7. #31 Editing: Local inpainting cleanup - artifact repair workflow
8. #32 Variants: Campaign output generator - local ratio and preset batch production
9. #33 Events: Branded social still builder - local event-template production
10. #34 Validation: Synthetic image fixture generator - private regression dataset pipeline
11. #35 UI: Local AI services console - GDS operator control surface
12. #36 Analytics: Local AI service reporting - zero-cost value and operational metrics
```

Milestones to confirm on the issues:

```text
#25-#26 -> Local AI Services 0 - Foundation Contracts
#27-#31 -> Local AI Services 1 - Product Studio Pipelines
#32-#36 -> Local AI Services 2 - Operator Experience and Validation
```

Labels to confirm:

```text
local-ai-service
zero-external-cost
ops
observability
```

Additional labels by issue:

```text
#25 -> api-contract, model-governance
#26 -> model-governance
#27 -> product-studio
#28 -> product-studio
#29 -> quality-gate
#30 -> quality-gate
#31 -> product-studio
#32 -> product-studio
#33 -> product-studio
#34 -> quality-gate, model-governance
#35 -> gds-required, accessibility, operator-ui
#36 -> observability
```

Desired GitHub Projects v2 status:

```text
#25-#36 -> Done
```

Project board readme section to add or confirm:

```md
## Current Next Pack: Local AI Services - Zero External Cost v1

Canonical standard: every issue in this pack follows sovereignsquad/general-design-system#81.

Scope: build high-value local AI image services on the existing try-on/local worker stack without paid external inference or API costs in the first delivery.

Delivery sequence:
1. #25 Platform service registry and capability contract
2. #26 Local model pack governance
3. #27 Garment isolation pipeline
4. #28 Product photo cleanup pipeline
5. #29 Brand safety analyzer
6. #30 Try-on quality gate
7. #31 Local inpainting cleanup
8. #32 Campaign variant generator
9. #33 Event social still builder
10. #34 Synthetic regression fixture generator
11. #35 GDS local AI services console
12. #36 Local AI service reporting

Pack rules:
- zero paid external inference/API cost is mandatory for first delivery
- all UI/UX/frontend work must use only the Sovereign Squad General Design System
- accessibility is mandatory
- every issue must include architecture, contracts, APIs/CLI where relevant, runtime behavior, observability, retries/timeouts, rollback, tests, documentation, dependencies, and execution order
- no issue may ship as a vague umbrella task
```

GraphQL quota state observed during delivery:

```json
{"limit":5000,"remaining":0,"reset":1780772498,"used":5000}
```

The reset timestamp corresponds to `2026-06-06 21:01:38 CEST` in the local environment used for delivery.

Suggested follow-up verification:

```bash
gh api rate_limit --jq '.resources.graphql'
gh project item-list 41 --owner moldovancsaba --format json --limit 100
```

If the issues are missing from the project board after quota reset, add each issue to project `41` and set status to `Done` using GitHub Projects v2 tooling or the `gh project` commands available in the local CLI version.

Latest delivery continuation note:

- A follow-up board attempt still showed GraphQL quota exhausted.
- `gh project item-list 41 --owner moldovancsaba --format json --limit 10` returned `unknown owner type` in that quota-exhausted state.
- Before mutating cards later, first re-confirm the project metadata and supported CLI syntax with:

```bash
gh project list --owner moldovancsaba --format json
gh project view 41 --owner moldovancsaba --format json
gh project field-list 41 --owner moldovancsaba --format json
```

If the local `gh project` command still reports `unknown owner type` after quota reset, use the GitHub web UI for the final board mutation or use GraphQL directly through `gh api graphql` with the project id from `gh project view`.

Direct GraphQL fallback plan after quota reset:

1. Discover the user project id, fields, and options.

```bash
gh api graphql -f query='
query {
  user(login: "moldovancsaba") {
    projectV2(number: 41) {
      id
      title
      fields(first: 50) {
        nodes {
          ... on ProjectV2Field { id name }
          ... on ProjectV2SingleSelectField {
            id
            name
            options { id name }
          }
        }
      }
    }
  }
}'
```

2. Discover existing project items for issues `#25-#36`.

```bash
gh api graphql -f query='
query {
  repository(owner: "moldovancsaba", name: "try-on") {
    issues(first: 12, filterBy: {states: CLOSED}) {
      nodes {
        number
        title
        id
        projectItems(first: 20) {
          nodes {
            id
            project { id number title }
          }
        }
      }
    }
  }
}'
```

3. For any missing issue, add it to project `41` with `addProjectV2ItemById`.

```bash
gh api graphql -f query='
mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) {
    item { id }
  }
}' -F project='PROJECT_ID_FROM_STEP_1' -F content='ISSUE_NODE_ID'
```

4. Set status to `Done` with `updateProjectV2ItemFieldValue`.

```bash
gh api graphql -f query='
mutation($project: ID!, $item: ID!, $field: ID!, $done: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $project,
    itemId: $item,
    fieldId: $field,
    value: { singleSelectOptionId: $done }
  }) {
    projectV2Item { id }
  }
}' -F project='PROJECT_ID_FROM_STEP_1' -F item='PROJECT_ITEM_ID' -F field='STATUS_FIELD_ID' -F done='DONE_OPTION_ID'
```

GraphQL fields to capture in the later run:

```text
PROJECT_ID
STATUS_FIELD_ID
DONE_OPTION_ID
ISSUE_NODE_ID for #25-#36
PROJECT_ITEM_ID for #25-#36
```

Suggested follow-up command sequence after quota reset:

```bash
gh project item-list 41 --owner moldovancsaba --format json --limit 200
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/25
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/26
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/27
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/28
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/29
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/30
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/31
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/32
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/33
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/34
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/35
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/36
```

After adding or confirming the items, use the project field IDs from `gh project field-list 41 --owner moldovancsaba --format json` and set each item status to `Done`. The exact `gh project item-edit` command depends on the local `gh` CLI field metadata returned after quota reset.

### 2026-06-07 follow-up verification

Repository issue state was verified with `gh issue view`:

- `#25` through `#36` exist in `moldovancsaba/try-on`.
- All `#25` through `#36` are closed.
- Required labels and milestones match the handover contract.

Project metadata was partially verified before GraphQL quota was exhausted again:

```text
Project: {try-on} - From IDEA to LIVE
Project number: 41
Project id: PVT_kwHOACGtF84BXhZM
Status field id: PVTSSF_lAHOACGtF84BXhZMzhStsPg
Done option id: 98236657
```

The current project board readme is already occupied by a newer pack:

```text
Current Next Pack: Try-On Runtime QA, Isolation, and Analytics v2
```

Decision still required before mutating the project readme:

- keep the newer Runtime QA pack as the current board focus, and only verify/add/move `#25-#36` to `Done`
- or replace/append the Local AI Services pack section from this handover

The follow-up attempt consumed nearly all GraphQL quota while reading Project v2 data:

```json
{"limit":5000,"remaining":9,"reset":1780844128,"used":4991}
```

The reset timestamp corresponds to `2026-06-07 16:55:28 CEST`.

Resume after quota reset with the smallest possible calls:

```bash
gh api rate_limit --jq '.resources.graphql'
gh project item-list 41 --owner moldovancsaba --format json --limit 200 \
  --jq '.items[] | select(.content.repository == "moldovancsaba/try-on" and (.content.number >= 25 and .content.number <= 36)) | {number:.content.number,status:.status,id:.id,url:.content.url}'
```

If any issue from `#25-#36` is missing, add it:

```bash
gh project item-add 41 --owner moldovancsaba --url https://github.com/moldovancsaba/try-on/issues/25
```

If any present item is not `Done`, set its status using:

```bash
gh project item-edit \
  --project-id PVT_kwHOACGtF84BXhZM \
  --id PROJECT_ITEM_ID \
  --field-id PVTSSF_lAHOACGtF84BXhZMzhStsPg \
  --single-select-option-id 98236657
```

### 2026-06-07 final project board update

GitHub Project v2 mutation completed after quota recovered:

- `#25` through `#36` were added to project `41`.
- `#25` through `#36` were verified on project `41`.
- `#25` through `#36` were set to Status `Done`.
- Project readme was preserved with the newer active `Current Next Pack: Try-On Runtime QA, Isolation, and Analytics v2`.
- A new completed-pack section was appended: `Completed Pack: Local AI Services - Zero External Cost v1`.

Verified project item status:

```text
#25 -> Done
#26 -> Done
#27 -> Done
#28 -> Done
#29 -> Done
#30 -> Done
#31 -> Done
#32 -> Done
#33 -> Done
#34 -> Done
#35 -> Done
#36 -> Done
```

Operational note:

- For Project v2 `Status` updates through `gh api graphql`, pass `singleSelectOptionId` with raw `-f done=98236657`; using typed `-F done=98236657` can make `gh` infer the wrong GraphQL variable type.
