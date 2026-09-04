# Release Notes

## Garment types, provider routing, and result storage - 2026-08-19 → 2026-08-28

**Garment types v1** (try-on#37, #38, #39): jobs can now carry a snapshot
`request.garmentType` (`motorsport_suit | jersey | top | bottom`) and
`request.sleeveStyle`. When present it drives render-category resolution instead of
the setup preset's category, adds an `expose_arms` mask mode for bare-armed
sleeveless renders, and (via `request.outfitBottomLeatherSuitId`) supports a
two-piece outfit as one atomic job — two sequential local passes, top before
bottom, one published result.

**Provider routing**: a garment-typed jersey/top/bottom job on a Segmind
(`segmind_idm_vton`) setup is now rerouted to FASHN v1.6 on fal — side-by-side
testing on live submissions showed FASHN preserves garment lettering and the
wearer's own lower body where IDM-VTON does not. Motorsport suits and
local/google-edge setups are never rerouted.

**Provider inputs go base64, not ImgBB**: both fal and Segmind now receive inputs
inline as base64 (fal as a data URI, Segmind raw) instead of fetching from ImgBB
URLs — a live ImgBB read-timeout degradation had been stalling every render on
the old path. A transparent-background garment is composited onto white before
reaching fal only (FASHN flattens alpha to black, previously misread as a long
sleeve); Segmind's own transparent-garment handling is unchanged.

**Security** (try-on#42): origin-guard middleware (cross-origin requests get 403)
and the render output path constrained to the project root.

**CI** (2026-08-23): a build gate that byte-compiles every source file and runs a
secret scan on push/PR to main. `.env.tryon-worker.example` now leads with the
fleet's `MONGODB_URI`/`MONGODB_DB` names (the worker already accepted both).

**Result storage** (2026-08-26): Vercel Blob is now the required primary result
store (`BLOB_READ_WRITE_TOKEN`); ImgBB becomes an optional best-effort mirror that
never fails publication. The source-image download host allowlist was also fixed
(it was live-broken).

**Bug fix** (2026-08-28): a setup loaded from Mongo (rather than the local JSON
catalog) lost its `config`, silently defaulting every such job to the MotoGP local
pipeline regardless of its real processing profile. Fixed, with regression
coverage.

Version unified to fleet 12.2.0 for this window. See `docs/RUNBOOK.md` for
operations and `docs/TRYON_ATLAS_CONTRACT.md` for the schema-level detail.

## Local model research and performance findings - 2026-08

Investigated whether a newer locally-hostable model could replace CatVTON/SD1.5, and
measured the current pipeline on the production machine for the first time.

Findings:

- Rendering is **memory-bound, not compute-bound**. Measured ~62 s/step against the
  ~2-2.5 s/step this hardware supports; the weights do not stay resident and re-fault
  from swap. A 50-step render takes ~52 minutes idle, 92 minutes under contention.
- **No larger model fits.** FLUX.2 klein 4B (Apache 2.0) peaked at 17.94 GB on a 16 GB
  machine. Qwen-Image-Edit needs 32 GB+. The Apache-licensed candidates have no virtual
  try-on weights, and producing them needs a GPU this project does not have.
- mflux's `in-context-catvton` is **not** a runtime swap for the current model: it loads
  FLUX.1-Fill-dev (12B, non-commercial).
- Documented the licence position: CatVTON weights are CC BY-NC-SA 4.0, and the BY term
  requires attribution that the app was not carrying. Added to README.

Recommended operating changes: keep other model servers unloaded during renders, and cut
steps from 50-84 to ~28.

Detail and numbers: `docs/LOCAL_TRYON_MODEL_RESEARCH.md`.

## Documentation and comment audit — 2026-08

Two audit passes over every first-party comment, the second scored against Google's
Python style guide §3.8, PEP 257, and the code-comment co-evolution research. The rules
are now written down in `docs/CODE_COMMENT_STANDARD.md`, with the scripts to re-run the
checks.

Behavior fix found by the audit:

- `tryon_quality_gate` passed the app root where `evaluate_model_packs` expects the
  models root, so the MediaPipe pack always evaluated as unavailable and pose
  validation was skipped for every job. The Google Edge analyzer was wired into the
  gate but never ran.

Comments corrected (no behavior change):

- The hand-preservation block claimed hands were "always" preserved; it never runs.
- The VAE precision comment described the opposite of the call it annotated.
- The texture-warp pass and its API fields are unreachable and now say so.
- `TRYON_POLL_INTERVAL_SECONDS` is documented but never read — the poll interval is
  held in worker settings. The README's `EXTERNAL_PROVIDER_*` / `OPTIONAL_PROVIDER_*`
  names are placeholders no code reads.

Documentation added:

- Module docstrings across `services/` and the operator CLIs in `scripts/`.
- Docstrings for the cross-module API, the render path, and the worker job lifecycle —
  coverage went from 20 documented definitions to roughly 70.
- `HANDOVER.md` rewritten; it had been describing the Google Edge lane as uncommitted
  work in progress long after it landed.

Also in this window: `pytest` added to `requirements.txt` (the operations playbook
already invoked it, but nothing installed it), and vendored `.pyc` files purged from
git history — clones predating 2026-08-14 must be re-cloned.

Validation:

```bash
./.venv311/bin/python -m pytest -q tests
```

## Local AI Services - Zero External Cost v1

Added a local-first image service family on top of the try-on stack.

Highlights:

- local service registry
- model pack readiness contract
- garment isolation pipeline
- product photo cleanup pipeline
- brand safety analyzer
- try-on quality gate
- local inpainting cleanup
- campaign variant generator
- event social still builder
- synthetic fixture generator
- local service reporting
- FastAPI endpoints
- CLI for operators and automation
- architecture, LLD, user guide, and tests

The first implementation uses deterministic local image operations and introduces no paid external inference/API cost.

Validation:

```bash
./.venv311/bin/python -m unittest tests.test_local_ai_services
```

GitHub handover:

- Issues `#25-#36` are implemented, commented, and closed.
- Native GitHub Projects v2 card/status updates are pending GraphQL quota reset.
- See `docs/LOCAL_AI_SERVICES.md` for exact board follow-up steps.
