# Project board review and manual movement / Scrum journal

Reviewed: 2026-11-15 UTC. Repository: moldovancsaba/try-on.

Canonical board: [project board](https://github.com/users/moldovancsaba/projects/41). **The board is the owner's source of truth and is read-only. Never add, remove, reorder or move cards or edit project fields.** This file records observations and conditional proposals for manual owner decisions only.

## Verified coverage and limitations

- Repository collection: 55 entries, including 50 issues and 5 pull requests; 3 issues are open.
- Board: all 52 returned items were read; totalCount matches the captured item count.
- Linked issue-state / Done-status discrepancies: 0. Open issues absent from this board snapshot: 2. Absence here does not prove absence from every board and does not authorize creating a card.
- Exact title-and-body duplicate groups among open issues: 0. Similar titles alone were not treated as duplicates.
- Issue and board reads succeeded. No access failure was observed for these captured collections. No board write was attempted.
- This is a documentation-only review. No runtime suite, production flow, user data mutation, external message, dependency upgrade or deployment action was performed. Source presence and previous Done status are not fresh acceptance evidence.

## Source observations and work clusters

### Deliberately paused; no restart implied

HANDOVER records the owner's intentional pause and disabled local services. Do not interpret an unavailable port as an incident or restart workers during a documentation review. #52 explicitly requires excluding jobs owned by image.direct before any re-enable; historical closed worker/provider tickets do not remove that ownership hazard. The proposed partner gateway is documented as unbuilt, not current behavior. Camera integration changes must be checked on both sides before reactivation.

### Licensing and residual documentation acceptance

#51's CatVTON/vendor licensing question remains a separate unresolved owner/legal decision; a repository-level MIT file is not proof every model/vendor asset has equivalent rights. Do not select or download replacement model weights without license and quality evaluation. #40's audit proposal already has API-reference/runbook/inventory documents; reconcile current route/auth/provider contracts and remaining acceptance rather than recreating the documents or certifying deployment. Closed duplicate-looking #6/#7 are preserved as history.

### Modernization boundaries

The app is Gradio/FastAPI, with GDS principles adopted through its local CSS/templates; importing a Mantine application shell is not the applicable upgrade. Newer try-on providers or local diffusion models must be evaluated for garment category, identity/brand preservation, masking, latency, memory, license, cost limits and human-reviewed quality. Preserve durable lease/retry, result moderation and ownership contracts instead of replacing them with provider-specific behavior. No render, service restart, model download, live job claim, Atlas access or settings write occurred.

### Actual verification limits

Python compilation and initial inventory/link check passed (254 links; shallow freshness skipped). The pytest command could not start because pytest is absent. The first synthetic mask smoke could not import torch; therefore the complete six-smoke sequence and full suite are unverified. This environment has no repository .venv311 runtime. These limitations are recorded rather than treating source presence or old Done cards as acceptance.

Inspected sources: [README.md](../README.md), [AGENTS.md](../AGENTS.md), [HANDOVER.md](../HANDOVER.md), [docs/RUNBOOK.md](../docs/RUNBOOK.md), [docs/TRYON_ATLAS_CONTRACT.md](../docs/TRYON_ATLAS_CONTRACT.md). Documentation paths and register coverage were checked before publication. No new technology is selected or installed by this assessment.

## Per-card review register

A destination is a proposal, never an executed move. Historical closed cards stay historical; migrated or not-planned closures do not prove feature delivery. Open work remains subject to its issue's acceptance criteria and owner decision.

| Issue / card | Title | Issue state / reason | Observed current column | Proposed destination / next evidence | Owner decision / manual execution |
| --- | --- | --- | --- | --- | --- |
| [#7](https://github.com/moldovancsaba/try-on/issues/7) | Contracts: Camera pipeline - versioned Atlas job and suit schemas | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#8](https://github.com/moldovancsaba/try-on/issues/8) | API: MotoGP processing - explicit worker-to-try-on execution profile | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#9](https://github.com/moldovancsaba/try-on/issues/9) | Security: Remote asset intake - validated host allowlist and secret-safe logging | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#10](https://github.com/moldovancsaba/try-on/issues/10) | Runtime: Queue worker - durable lease, retry, and offline catch-up state machine | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#11](https://github.com/moldovancsaba/try-on/issues/11) | Delivery: Result publishing - idempotent ImgBB upload and Camera completion callback | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#12](https://github.com/moldovancsaba/try-on/issues/12) | Operations: Local worker service - launchd lifecycle, restart, and log management | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#13](https://github.com/moldovancsaba/try-on/issues/13) | Observability: Queue processing - diagnostics, job events, and health surfaces | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#14](https://github.com/moldovancsaba/try-on/issues/14) | UI: Worker control center - accessible polling, queue state, and operator controls | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#15](https://github.com/moldovancsaba/try-on/issues/15) | Reliability: Provider SLA routing, timeout policy, and circuit breaker | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#16](https://github.com/moldovancsaba/try-on/issues/16) | Performance: Queue concurrency controller with safe parallelism limits | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#17](https://github.com/moldovancsaba/try-on/issues/17) | Reliability: Camera backpressure when try-on queue is overloaded | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#18](https://github.com/moldovancsaba/try-on/issues/18) | Observability: Provider performance scorecard with latency and failure tracking | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#19](https://github.com/moldovancsaba/try-on/issues/19) | Reliability: Replay and reconciliation audit across Atlas, Camera, and image hosting | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#20](https://github.com/moldovancsaba/try-on/issues/20) | Operations: Synthetic canary job and alerting for worker/provider health | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#21](https://github.com/moldovancsaba/try-on/issues/21) | Maintainability: Failed-job taxonomy with automatic operator notes | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#22](https://github.com/moldovancsaba/try-on/issues/22) | Performance: Load test plan and throughput benchmark for queue processing | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#23](https://github.com/moldovancsaba/try-on/issues/23) | Operations: Disaster recovery runbook for worker, database, and callbacks | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#24](https://github.com/moldovancsaba/try-on/issues/24) | Governance: Cost and rate-limit controls per provider | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#58](https://github.com/moldovancsaba/camera/issues/58) | Try-On E2E: Disposable database guard - prevent fixture writes to operational Atlas | Issue | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#59](https://github.com/moldovancsaba/camera/issues/59) | Try-On E2E: Fixture lifecycle cleanup - run-scoped bootstrap and teardown | Issue | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#60](https://github.com/moldovancsaba/camera/issues/60) | Try-On E2E: Safe runner contract - one-command local validation | Issue | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#61](https://github.com/moldovancsaba/camera/issues/61) | Try-On Rerun: Superseded state contract - distinguish quality reruns from rejection | Issue | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#62](https://github.com/moldovancsaba/camera/issues/62) | Try-On Rerun: Admin archive visibility - GDS superseded filter and card state | Issue | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#63](https://github.com/moldovancsaba/camera/issues/63) | Try-On Rerun: HiTL lifecycle E2E - prove rerun does not auto-approve | Issue | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#64](https://github.com/moldovancsaba/camera/issues/64) | Try-On Identity: Unrecoverable classification contract - reviewed Guest records | Issue | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#65](https://github.com/moldovancsaba/camera/issues/65) | Try-On Identity: Admin review workflow - GDS correction and unrecoverable marking | Issue | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#66](https://github.com/moldovancsaba/camera/issues/66) | Try-On Analytics: Funnel contract - lifecycle and superseded rerun metrics | Issue | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#67](https://github.com/moldovancsaba/camera/issues/67) | Try-On Analytics: GDS funnel UI - accessible pipeline visualization | Issue | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#68](https://github.com/moldovancsaba/camera/issues/68) | Try-On Analytics: Section exports - filtered CSV/JSON operational reports | Issue | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#25](https://github.com/moldovancsaba/try-on/issues/25) | Platform: Local AI service registry - zero-cost capability contract | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#26](https://github.com/moldovancsaba/try-on/issues/26) | Models: Local AI model pack governance - inventory, health, and rollout contract | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#27](https://github.com/moldovancsaba/try-on/issues/27) | Studio: Garment isolation - reusable transparent asset pipeline | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#28](https://github.com/moldovancsaba/try-on/issues/28) | Studio: Product photo cleanup - local catalog image workflow | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#29](https://github.com/moldovancsaba/try-on/issues/29) | Quality: Brand safety analyzer - logo and text preservation scoring | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#30](https://github.com/moldovancsaba/try-on/issues/30) | Quality: Try-on auto-review gate - local pass, warn, and rerun recommendation flow | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#31](https://github.com/moldovancsaba/try-on/issues/31) | Editing: Local inpainting cleanup - artifact repair workflow | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#32](https://github.com/moldovancsaba/try-on/issues/32) | Variants: Campaign output generator - local ratio and preset batch production | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#33](https://github.com/moldovancsaba/try-on/issues/33) | Events: Branded social still builder - local event-template production | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#34](https://github.com/moldovancsaba/try-on/issues/34) | Validation: Synthetic image fixture generator - private regression dataset pipeline | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#35](https://github.com/moldovancsaba/try-on/issues/35) | UI: Local AI services console - GDS operator control surface | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#36](https://github.com/moldovancsaba/try-on/issues/36) | Analytics: Local AI service reporting - zero-cost value and operational metrics | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#37](https://github.com/moldovancsaba/try-on/issues/37) | Worker: honor garmentType/sleeveStyle from the job request — per-provider category resolution | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#38](https://github.com/moldovancsaba/try-on/issues/38) | Local pipeline: bare-arm skin synthesis for sleeveless garments (expose_arms mask mode) | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#39](https://github.com/moldovancsaba/try-on/issues/39) | Worker: two-pass outfit rendering — top then bottom, one atomic job, one result | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#42](https://github.com/moldovancsaba/try-on/issues/42) | Security: try-on app-server - origin fencing and render-path containment | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#43](https://github.com/moldovancsaba/try-on/issues/43) | Docs: Operator truth - rewrite HANDOVER and README for the 2026-08-19 provider changes | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#44](https://github.com/moldovancsaba/try-on/issues/44) | API Reference: tryon - 31 endpoints at 100% coverage + deprecation list | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#45](https://github.com/moldovancsaba/try-on/issues/45) | Refactor+Ops: Remove dead code and add queue/ retention policy | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#46](https://github.com/moldovancsaba/try-on/issues/46) | Docs: Fix Segmind/retry docstrings + comment standard + RUNBOOK + fit-in-fleet page | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#47](https://github.com/moldovancsaba/try-on/issues/47) | Test: try-on - regression coverage for _load_setup_by_id's Mongo-config path | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#48](https://github.com/moldovancsaba/try-on/issues/48) | Docs: try-on - correct README's stale setup-resolution passages and document Camera's Atlas-config-write capability | closed / completed | Done | Remain; preserve historical evidence, no fresh completion claim. | Pending / not executed |
| [#51](https://github.com/moldovancsaba/try-on/issues/51) | Investigate vendored CatVTON licence against the repo's MIT licence | open / unspecified | Backlog (SOONER) | Remain; satisfy original issue acceptance before proposing Review or Done. | Pending / not executed |
| [#52](https://github.com/moldovancsaba/try-on/issues/52) | Worker: claim query - skip jobs owned by image.direct before any re-enable | open | Not on this board | No card change; inspect associated tracking and acceptance evidence. | Pending / not executed |
| [#40](https://github.com/moldovancsaba/try-on/issues/40) | Fleet audit P2+P3 (try-on): API reference (31 routes) + drift register + runbook | open | Not on this board | No card change; inspect associated tracking and acceptance evidence. | Pending / not executed |

## Append-only Scrum / manual movement journal

| Date / Scrum reference | Board and issue | Verified current column | Proposed destination | Evidence / rationale | Dependency / blocker | Owner decision | Manual execution / person | Verified new column | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-15 review | Board and issues above | Snapshot above | No automatic move | Recorded source/issue/board review | See unresolved requirements | Pending | Not executed | Snapshot only | Preserve history |
| YYYY-MM-DD / meeting link | Board URL + issue URL | Exact observed value | Exact proposed value or remain | Acceptance link | Specific condition | Pending | Not executed | Not verified | — |

Read the issue and actual board before each future proposal. Retain previous observations when a decision changes. Record execution only after the owner performs it and the result is read back. Documentation rollback requires no product or board rollback.
