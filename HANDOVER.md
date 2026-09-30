# Handover — Try-On Studio

_Last updated: 2026-09-28_ (verified @ 1ccd284; fleet version 12.3.40)

Snapshot of where the repo is for the next person picking it up.

## Recent work (2026-09-04 → 09-28)
- Security (try-on#42, 28a76c2, 2026-09-04): `POST /api/tryon/run` and
  `POST /api/worker/service-action` require the `x-tryon-local-secret` header, equal to
  `TRYON_LOCAL_SECRET` from `.env.tryon-worker`. Missing or wrong → 401; if the secret is
  unset both routes refuse everything. The worker and `scripts/ab_render_expose_arms.py`
  send it; the Worker Control page receives it server-side.
- v12.2.1 (899ee12, 2026-09-08): `tryon_infra_cli.py sweep-processing`; dead code removed
  (hand-preserve mask, texture-warp branch, `scripts/recover_fal_fallen_jobs.py`); full
  API reference, fleet page, and the inventory gate in CI.
- v12.2.2 / v12.2.3 (c8ba623, f1d79db): the docs gate (`scripts/fleet-audit-inventory.py
  --check`) also fails on broken links and checks contract stamps; since 553fa20 an
  unresolvable stamp or one more than 90 commits behind HEAD fails it.
- Lockstep version bumps 12.3.28 → 12.3.35 (2026-09-08 → 09-12), version only.
- 1ccd284 (2026-09-12): `.config/settings.json` moved to Full-Body, 60 steps, mask
  sharpness 16. Those are exactly the values a `default_motogp` render writes back, so
  this appears to be leftover render output, not hand tuning. The file only holds the
  UI's default control values and is rewritten by every render (README, "App settings
  path"); untracking it is an open owner decision.
- 12.3.37 (2026-09-28): fleet lockstep release; docs sweep;
  `scripts/smoke_local_api_fencing.py` now reads the version from `app.py` and checks the
  401 secret gate instead of hard-coding a version (it had been failing since 12.2.1).

## Earlier work (2026-08-19 → 09-03)
- Garment types v1: garmentType/sleeveStyle resolution, `expose_arms` mask mode,
  two-pass outfit rendering (top→bottom).
- Provider routing: garment-typed jersey/top/bottom on a Segmind setup reroute to
  FASHN v1.6 (fal). Motorsport suits + local/google keep their pipeline.
- Provider inputs are base64 now (fal data-URI, Segmind raw); no ImgBB round-trip on
  the input path.
- Transparent garments are white-composited before fal only (FASHN flattens alpha to
  black, which read as long sleeves on the Debrecen jersey). Segmind handles
  transparent garments through a separate mechanism (forced `dresses` category +
  alpha-edge prompt), not white-compositing.
- Security (try-on#42): origin-guard middleware (cross-origin→403) + render output
  path constrained to the project root. The shared-secret gate followed on 2026-09-04
  (above).
- Version unified to fleet 12.2.0 at the time; lockstep with the fleet since 12.3.28,
  now 12.3.37. See `docs/RUNBOOK.md` for operations.
- Cross-app changes follow the fleet contract-first rule
  (`docs/_audit/contract-first-rule.md`); try-on's place in the fleet is summarized in
  `docs/_audit/tryon-in-the-fleet.md`, its routes in `docs/_audit/api-reference.md`.
- CI added (2026-08-23): byte-compiles every source file and runs a working-tree
  secret scan on push/PR to main. Deliberately skips installing `requirements.txt`
  (torch/diffusers/mediapipe/ultralytics are gigabytes and still wouldn't exercise
  GPU paths). `.env.tryon-worker.example` now leads with the fleet's `MONGODB_URI`/
  `MONGODB_DB` names (the worker has always accepted both spellings).
- Result storage (2026-08-26): Vercel Blob is now the required primary result store
  (`BLOB_READ_WRITE_TOKEN`); ImgBB is demoted to a best-effort mirror that never
  fails publication (`IMGBB_API_KEY` optional). Companion to camera's same-night
  migration. Also fixed the source-image download host allowlist, which was
  live-broken.
- Bug fix (2026-08-28): `_load_setup_by_id`'s Mongo fallback hardcoded `config` to
  `{}`, so any setup absent from the local `.config/tryon_setups.json` seed (e.g. one
  created straight from Camera's admin UI) silently rendered through the MotoGP local
  pipeline regardless of its real `processing_profile`. Fixed to copy the real config
  through; regression test added. README's "Atlas is metadata-only" passages
  described this exact bug as intended architecture — corrected.

## Runtime status

- Both launchd services running as of 2026-09-28: `com.tryon.app-server` (up since boot,
  never exited) and `com.tryon.camera-worker` (heartbeat fresh). The worker exited 1 once
  at the 02:22 boot: Atlas SRV lookup failed with DNS `NoNameservers` because the network
  was not up yet, and launchd restarted it (`last exit code = 1` in `launchctl print` is
  that event, not a crash loop). The running app-server still reports 12.3.35 until its
  next restart.
- App serves `http://127.0.0.1:7860`; `GET /api/capabilities` reports all core vault
  assets ready.
- Queue retention (try-on#45): `scripts/tryon_infra_cli.py prune-queue` trims
  `queue/done` + `queue/failed` by age and count; `sweep-processing` reconciles
  `queue/processing` against Atlas and moves only `done`/`failed` workspaces (leased,
  stale-lease and record-less dirs are reported, never moved). Both dry-run unless
  `--apply`; see `docs/RUNBOOK.md`. Atlas-side consistency is still `reconcile`.
- Test suite green (regression coverage for the Mongo config-passthrough fix added
  2026-08-28). `pytest` is provisioned by `install.sh`.
- Canary still has not been run; `.runtime/canary_status.json` does not exist (checked
  2026-09-28).

## Recently landed

### Local model research (2026-08-14)

Full findings: [docs/LOCAL_TRYON_MODEL_RESEARCH.md](docs/LOCAL_TRYON_MODEL_RESEARCH.md).

The headline is that **throughput here is memory-bound, not compute-bound, and no larger
model is viable.** A 50-step render measured ~52 minutes idle and 92 minutes with a second
image model resident, against ~2-2.5 s/step the M4 can actually do. The app's weights do
not stay resident, so it re-faults from swap every step.

Do not spend effort on model replacement. FLUX.2 klein 4B was tested and peaked at
17.94 GB on this 16 GB machine; mflux's CatVTON path turns out to need FLUX.1-Fill-dev
(12B, non-commercial), so it is not the cheap runtime swap it appears to be. The cheap
wins are keeping other model servers (Ollama was holding 3.6 GB) unloaded during renders
and cutting steps from 50-84 to ~28.

The step cut was not applied (as of 2026-09-28): `default_motogp` and
`.config/settings.json` use 60 steps, and the MotoGP profile enforces at least 50
(app.py:1075, :1638), so ~28 is below the floor on that route. At ~62 s/step a default
local render takes about an hour. Open question for the owner: keep that trade-off, or
lower the preset and the enforced floor.


### Google AI Edge / MediaPipe lane

Landed and committed — the previous handover listed this as in-flight WIP, which was
stale. Model pack `google_edge_mediapipe`, the `google_edge_analyzer` service, the
`google_edge_tryon` overlay preset (rank 25), and `mediapipe` in requirements.

**One fix during the August audit:** `tryon_quality_gate` passed `app_root` where
`evaluate_model_packs` expects the models root, so the pack always read "unavailable"
and pose validation was silently skipped for every job. The analyzer was wired in but
never actually running. Fixed; the gate now consults it for real.

### Comment and documentation audit (August 2026)

Two passes, the second scored against the rules now written down in
[docs/CODE_COMMENT_STANDARD.md](docs/CODE_COMMENT_STANDARD.md).

Corrected claims that were actively false:

- `app.py` promised hands were "always" preserved from the source photo; the block
  never runs, because `preserve_hands` is forced off with the other fidelity
  overrides. Hands are still protected upstream by AutoMasker — but not by the code
  the comment pointed at.
- The "Precision VAE Handshake" comment described fp32-on-MPS while sitting on a call
  passing fp16. The rule it describes lives in the vendored pipeline, not there.
- The texture-warp pass (`warp_repair.py`) is unreachable for the same reason and is
  now labelled at every site, including the API fields that accept and discard it.
- `TRYON_POLL_INTERVAL_SECONDS` is documented but read nowhere; the poll interval
  lives in worker settings. The README env block's `EXTERNAL_PROVIDER_*` and
  `OPTIONAL_PROVIDER_*` names are genericized placeholders that no code reads.

Docstring coverage went from 20 definitions to ~70, concentrated on the cross-module
API in `services/`, the render path, and the worker's job lifecycle.

### GDS 3.9 adoption

Dependency baseline moved to `@sovereignsquad/gds*@3.9.0` and the operator-surface
roadmap in `docs/GDS_LOCAL_ADOPTION.md` is implemented against the Jinja/CSS bridge.

- **Open:** `pnpm-lock.yaml` still references the old `@doneisbetter/*` scope.
  Regenerate with `pnpm install --lockfile-only` (blocked in-session because it
  re-applies the `minimumReleaseAgeExclude` supply-chain guard).
- **Deferred:** Library Rebuild/Download/Disable actions (no backend endpoints yet);
  full keyboard canvas point-placement on Setup Garment (kept 'U' undo).

### Repository history

Vendored `.pyc` files were purged from the full history and force-pushed, so any clone
predating 2026-08-14 is on a dead branch and must be re-cloned. `.gitignore` already
covered those paths; they had been committed before the rules landed.

## Known dead code, deliberately kept

Each is labelled in place; none of it runs:

- `warp_repair.py` — no runtime caller since the texture-warp branch in `app.py` was
  deleted (v12.2.1); kept because `tests/test_texture_repair.py` exercises
  `texture_repair_decision`. `enable_deep_texture`/`warp_strength` are still accepted
  on the wire and forced off.
- `validate_video_output` in `services/quality_contracts.py` — reads contract keys that
  no contract defines, so it raises KeyError on any call. Zero callers.

Deleted in v12.2.1 rather than kept: `_build_hand_preserve_mask` + the hand recomposite
block (hands are protected by the vendored masker and the composite step instead) and
`scripts/recover_fal_fallen_jobs.py` (one-off, zero references).

Decide to revive or delete what remains; leaving it is fine, leaving it *undocumented*
is what caused the audit findings.

## Notes for the next session

- Regenerate `pnpm-lock.yaml` (see above).
- Run the canary — it is the only end-to-end check nobody has exercised recently.
- ~100 definitions still meet a docstring trigger, mostly in `app.py`'s UI layer and
  the worker's provider plumbing. `docs/CODE_COMMENT_STANDARD.md` has the script that
  ranks them.
- `studio_tools/templates/worker_control.html` has 229 lines of JavaScript (one inline
  `<script>` block, lines 144-372 of the 375-line file) and no comments, against a
  well-commented `index.html` next door.
- Decide the render step count (see "Local model research" above) and whether
  `.config/settings.json` stays tracked; both are owner calls.
