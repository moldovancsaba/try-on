# Release Notes

## 12.3.37 — 2026-09-28

Fleet lockstep release (messmass, camera, fanmass, try-on, savetheworld). try-on had no
12.3.36; it goes from 12.3.35 straight to 12.3.37 to rejoin the fleet version.

- `scripts/smoke_local_api_fencing.py` no longer hard-codes a fleet version. It had
  asserted `version="12.2.0"` and failed on every release since 12.2.1. It now reads the
  version from `app.py`'s `FastAPI(version=...)` and only requires X.Y.Z, and it also
  checks, at source level, that `/api/tryon/run` and `/api/worker/service-action` carry
  the 401 `x-tryon-local-secret` gate. It still reads source only: no server, no render.
- Docs sweep: README (settings file, MotoGP floors, the `/api/tryon/run` secret header and
  fields, env block, route list, setup examples), HANDOVER, RUNBOOK (secret, boot-time DNS
  exit, log rotation, smoke-script index), the drift register and API reference line
  refs, `.env.tryon-worker.example`, a new `AGENTS.md`, and the contract-first rule now
  covers five repos.
- The ~28-step recommendation from the 2026-08 research was not adopted: the default setup
  and `.config/settings.json` use 60 steps and the MotoGP profile enforces at least 50
  (about an hour per local render at ~62 s/step). Documented as an open owner question;
  the preset is unchanged.

## 12.3.28 – 12.3.35 — 2026-09-08 → 2026-09-12

- Lockstep version bumps with the fleet (080aa9f, 57afc3c, 3579027, a4ff20c, 8caa18d,
  21a7582, e8d4672, 7fadd4a). No functional change beyond the docs-gate updates below.
- 12.3.30 (3579027) and 12.3.31 (a4ff20c): vendored scanner updates. Leading-slash links
  resolve repo-relative, freshness checks are skipped on shallow checkouts, and the
  scanner also reads `src/app/api`.
- 553fa20 (2026-09-09, messmass#346): the docs gate now fails, rather than warns, when a
  "verified @ sha" stamp does not resolve in this repo's history or is more than 90
  commits behind HEAD.
- 1ccd284 (2026-09-12, still 12.3.35): `.config/settings.json` changed to Full-Body,
  60 steps, mask sharpness 16. These appear to be the values a `default_motogp` render
  writes back, not hand tuning; the file only seeds the UI's default controls and
  every render rewrites it (README, "App settings path").

## 12.2.2 / 12.2.3 — 2026-09-08 (docs gate)

- 12.2.2 (c8ba623): `scripts/fleet-audit-inventory.py --check` also fails on broken
  relative markdown links and warns on stale contract stamps.
- 12.2.3 (f1d79db): freshness warnings only for this repo's own stamps.

## 12.2.1 — 2026-09-08 (queue sweep, dead-code removal)

- `tryon_infra_cli.py sweep-processing` (try-on#45): reconciles `queue/processing`
  against Atlas and moves only `done`/`failed` workspaces; leased, stale-lease and
  record-less dirs are reported, never moved. Dry-run unless `--apply`. Proof:
  `scripts/smoke_sweep_processing.py`.
- Dead code removed: the hand-preserve mask and recomposite, the texture-warp branch,
  `scripts/recover_fal_fallen_jobs.py`. `warp_repair.py` stays because
  `tests/test_texture_repair.py` imports it.
- `docs/_audit/api-reference.md` covers all 31 routes (try-on#44); fleet page
  `docs/_audit/tryon-in-the-fleet.md`; CI runs the inventory check (messmass#355).

## Local-secret gate on the control routes — 2026-09-04 (try-on#42, 28a76c2)

`POST /api/tryon/run` and `POST /api/worker/service-action` require the
`x-tryon-local-secret` header, equal to `TRYON_LOCAL_SECRET` from `.env.tryon-worker`.
Missing or wrong → 401; if the variable is unset both routes refuse every request. The
queue worker and `scripts/ab_render_expose_arms.py` send it; the Worker Control page
receives it server-side. Version stayed 12.2.0.

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
steps from 50-84 to ~28. (Status 2026-09-28: the step cut was not adopted; the default
setup uses 60 steps and the MotoGP profile enforces at least 50. See 12.3.37.)

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
