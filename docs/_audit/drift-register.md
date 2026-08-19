# try-on drift register — fleet audit P3, first edition

Generated 2026-08-19 against HEAD `f030d89` (audit at code `e2c92c6`) by the
fleet documentation audit (try-on#40; method in messmass#344). Every claim
carries file:line evidence. Verdicts: WRONG / STALE / MISSING / CURRENT.

## 0. Behavior / operational findings escalated (see try-on#41)
- **Zero auth on all 31 FastAPI routes**, protected solely by the loopback
  bind `uvicorn.run(app, host="127.0.0.1", port=7860)` (app.py:2497) — no
  middleware, no CORS/origin check, no token. Two routes are high-impact:
  `POST /api/tryon/run` takes arbitrary absolute input **and output** paths
  (app.py:1823-1828, :1876-1877) — a local caller (including a browser page
  hitting 127.0.0.1, since there is no origin check) can write a PNG anywhere
  the app-server user can; `POST /api/worker/service-action` shells launchd
  service control (app.py:2198-2229).
- **Unbounded queue growth**: `queue/done` = 501 dirs, `queue/failed` = 73,
  ~2 MB/job, none pruned since 2026-06-03; no retention policy in code.
- **Two orphaned `queue/processing/` workspaces** from June — one
  (`job_20260605111917_6183e3c0`) holds a completed `result.png` that was
  apparently never published. `queue/processing/` is never swept (a hard kill
  leaves the dir; only Atlas rows get reconciled). Run `tryon_infra_cli.py
  reconcile` on those jobIds before deleting.

## 1. WRONG (highest priority)
- **README.md:139 readiness gate**: "the worker checks app readiness before
  claiming a job." False — `run_once` (scripts/tryon_queue_worker.py:2896-2905)
  claims first, unconditionally; readiness is checked only inside the
  local/google-edge render branches. A Segmind or fal job is claimed and
  dispatched with no readiness check; a job claimed mid-model-load burns an
  attempt and lands in retry.
- **README.md:521-534** example uses `"processing_profile":"local_profile"` —
  not a valid profile; `normalize_processing_profile`
  (services/worker_contracts.py:70-86) silently returns `generic`, so a copy of
  this example does NOT get the MotoGP tuning a reader expects.
- **call_segmind_tryon_api docstring contradicts its own body 3 lines away**:
  scripts/tryon_queue_worker.py:2336-2337 says "inputs go through ImgBB first
  because the provider takes URLs"; :2352-2354 sends raw base64 and never
  touches ImgBB. Introduced by the most recent commit (e2c92c6), which added a
  correct inline comment without deleting the stale docstring. :2338-2340 also
  claims category is unconditionally forced to "dresses" — conditional since
  f22adf6 (:485-486).
- **app.py:2241-2242** worker_job_retry_api docstring: "Attempt count is left
  alone." Code zeroes `processing.attemptCount` when `resetAttempts` is true
  (:2299-2300); README:833 documents the reset correctly.
- **HANDOVER.md:100-102** "~100 definitions still meet a docstring trigger";
  running the standard's own script gives 313 triggered / 255 undocumented
  (254/196 excluding tests) — ~2× the stated backlog.
- **docs/TRYON_WORKER_FAILURE_CONTRACT_PLAN.md** specifies a final-failure
  callback with `processing.failureNotifiedAt`/`failureNotificationError`
  (:63-74); zero matches in first-party code — unimplemented, but written as
  contract with nothing marking it aspirational.
- **FAL_FULL_BODY_PROMPT never reaches fal**: FASHN v1.6 takes no prompt;
  `_coerce_fal_payload` (:1838-1850) has a fixed 10-key whitelist with no
  prompt field. The prompt only applies if a fal job falls back to Segmind;
  nothing says so, so a reader tuning FASHN by editing that string tunes nothing.

## 2. STALE (docs predating the 2026-08-19 changes)
- **HANDOVER.md is 9 commits / one full workday behind** — garment-type
  resolution, expose_arms, two-pass outfits, the mask_mode wrapper fix, the
  transparent-PNG category fix, the FASHN reroute, white compositing, and
  Segmind base64 are all absent. It is the designated entry point for the next
  person; the FASHN reroute (which changes which vendor renders and bills a
  jersey) is documented only in docs/TRYON_ATLAS_CONTRACT.md and no
  operator-facing doc.
- **README.md:125-127** worker flow still describes a single local-render
  pipeline; Segmind/fal/google-edge never call `/api/tryon/run`. FASHN reroute
  and base64 inputs absent (grep "FASHN" over README = 1 hit, the ImgBB line).
- **README.md:503-514** `/api/tryon/run` optional-field list omits `mask_mode`,
  `category_source`, `sleeve_length`, `pant_length` — the fields driving this
  week's behavior.
- **docs/TRYON_ATLAS_CONTRACT.md** (the cross-app contract, otherwise CURRENT):
  its only base64 statement is scoped to fal — Segmind's move to base64
  (e2c92c6, 38 min after the doc commit) and white-compositing of transparent
  garments (876045a) are undocumented; a camera-side reader would still believe
  Segmind fetches from ImgBB.
- **docs/RELEASE_NOTES.md** — no entry for any of the nine 2026-08-19 commits.
- **.env.tryon-worker.example:20** `SEGMIND_API_TIMEOUT_SECONDS=180` vs code
  default 120 (:317) — commit 0b479c0 changed the example, not the code.

## 3. CURRENT (verified — the good news)
- **docs/TRYON_ATLAS_CONTRACT.md is the most accurate doc in the fleet**: every
  schema/state/normalization/routing/outfit/expose_arms claim verified against
  code (schema V1 at worker_contracts.py:162-197; routing at
  tryon_queue_worker.py:448-465; expose_arms at app.py:802-804). Only the two
  omissions in §2.
- README worker-status fields, retry-endpoint rules (all six), outfit and
  expose_arms descriptions, critical-infra CLI — all verified CURRENT.
- docs/CODE_COMMENT_STANDARD.md scripts all run clean; the Aug-2026 hot-path
  comments are best-in-fleet (they name the incident, the garment, the symptom).

## 4. MISSING / camera-side verification required
- Contract claims unverifiable from this repo, to confirm against camera code
  in P1: completion endpoint idempotent by jobId; which callback id-shape camera
  accepts (the worker probes up to 16 POSTs/job over 4 id candidates ×4 shapes,
  tryon_queue_worker.py:1385-1421); whether `source.eventMongoId`/`eventId`
  (read at :1409-1411) are fields camera writes — they are NOT in the contract's
  tryon_jobs schema.
- docs/CODE_COMMENT_STANDARD.md:129-131 says `vendor/` is exempt as upstream,
  but vendor/CatVTON/model/cloth_masker.py:204-306 now carries a first-party
  expose_arms modification — the vendored fork is load-bearing for a shipped
  feature; the standard's framing no longer holds.

## 5. Obsoletion queue
- Dead-but-labelled: warp_repair.py + app.py:992-999 (enable_deep_texture forced
  False at :722; one test importer), `_build_hand_preserve_mask` (preserve_hands
  False at :724), `validate_video_output` (KeyError on any call, zero callers).
- Dead constant `FAL_FULL_BRAND_PROMPT` (:77).
- `scripts/recover_fal_fallen_jobs.py` — one-off, zero references, self-labelled.
- `outputs/` (2.8 MB, zero code references, not gitignored — untracked debris
  reappears in git status).
- The three 2026-08-19 smoke scripts (smoke_expose_arms_mask,
  smoke_garment_type_resolution, smoke_outfit_orchestration) are the ONLY
  verification for this week's features (tests/ covers none) yet are referenced
  nowhere — document them in README ops or move to tests/ before they rot.
- app.py:1498/:1506 original `/upload_garment` + `/save_package` handlers are
  replaced at runtime (:1941-1942) by path-sanitizing versions; the originals
  are unreachable but read as live path-traversal bugs 400 lines up.

## 6. Comment health
- Rule 3/5 adherence strong in touched hot paths; rule 1 (docstrings) weak —
  255/313 triggered defs undocumented. Zero TODO/FIXME; ~zero commented-out code.
- Contradicting comments: see §1 (Segmind docstring, retry docstring, FAL prompt).
- studio_tools/templates/worker_control.html — 229 lines of script, zero
  comments, the operator's only view of worker/queue/provider state.
