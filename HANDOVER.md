# Handover — Try-On Studio

_Last updated: 2026-07-03_

Snapshot of where the repo is for the next person picking it up.

## Runtime status

- Both launchd services healthy: `com.tryon.app-server` and `com.tryon.camera-worker`.
- App serves `http://127.0.0.1:7860`; models load in the background (`GET /api/capabilities` reports readiness).
- Queue is idle. Lifetime totals at last check: ~490 `done`, 17 `failed`, nothing stuck.
- Canary (`scripts/tryon_canary.py`) has not been run recently — `.runtime/canary_status.json` is empty.

## Recently landed

### GDS 3.9 migration + adoption
- Migrated the GDS dependency baseline from `@doneisbetter/gds*` → `@sovereignsquad/gds*@3.9.0`
  (`package.json`, `pnpm-workspace.yaml`, `docs/GDS_LOCAL_ADOPTION.md`). Pure scope rename; no API change.
  - **Open:** `pnpm-lock.yaml` still references the old scope. Regenerate with `pnpm install --lockfile-only`
    (blocked in-session because it re-applies the `minimumReleaseAgeExclude` supply-chain guard).
- Implemented the GDS operator-surface roadmap from `docs/GDS_LOCAL_ADOPTION.md` against the
  Jinja/CSS bridge (`studio_tools/static/global.css` + templates). All six items done:
  1. Worker Control — status badges, StateBlock for degraded health, backpressure + oldest-ready age.
  2. Provider Scorecard panel (success %, p50, fail/timeout/slow, circuit state) from `/api/worker/status`.
  3. Garment Library — card grid + empty state; `View Package` via new `/packages` static mount.
  4. Setup Garment — `alert()` → inline `aria-live` notices; dropzone states + type/size validation.
  5. Landing/nav — CSS-only mobile collapse, `aria-current`, reduced-motion hover.
  6. Gradio pages — server-rendered ops banner (model readiness + worker state).
- Verified live: app restarted, all pages 200, ops banner renders on the Gradio surface.
- **Deferred:** Library Rebuild/Download/Disable actions (no backend endpoints yet);
  full keyboard canvas point-placement on Setup Garment (kept 'U' undo).

## In flight (uncommitted WIP — not yet finished)

### Google AI Edge / MediaPipe lane
Work-in-progress in `services/local_ai_services.py`, `services/capabilities.py`, `services/tryon_setups.py`,
`services/worker_contracts.py`, `services/model_sync.py`, `scripts/tryon_queue_worker.py`,
`.config/tryon_setups.json`, `requirements.txt`, and tests (`tests/test_worker_google_edge.py`,
`tests/test_local_ai_services.py`, `tests/test_worker_contracts.py`):
- New model pack `google_edge_mediapipe` (`processors/google-edge-mediapipe/` with
  `pose_landmarker_full.task` + `face_landmarker.task`).
- `google_edge_analyzer` service — pose/face landmark validation, wired into `tryon_quality_gate`.
- `google_edge_tryon` service + preset — instant keypoint-guided overlay preview.
- `mediapipe` added to `requirements.txt`.
- **Next:** confirm the `.task` model files exist in the shared vault, run the new tests, and finish
  wiring the overlay service before advertising it. Keep this lane local-only per the services contract.

## Notes for the next session
- Regenerate `pnpm-lock.yaml` (see above).
- Run the canary and the Google Edge tests to confirm green before further changes.
- Related board handover for the Local AI Services pack lives in `docs/LOCAL_AI_SERVICES.md`.
