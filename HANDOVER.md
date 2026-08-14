# Handover — Try-On Studio

_Last updated: 2026-08-14_

Snapshot of where the repo is for the next person picking it up.

## Runtime status

- Both launchd services healthy: `com.tryon.app-server` and `com.tryon.camera-worker`.
- App serves `http://127.0.0.1:7860`; `GET /api/capabilities` reports all core vault
  assets ready.
- Queue idle, provider scorecard clean — no failures, no open circuits.
- Test suite green: 51 passed. `pytest` is now provisioned by `install.sh`.
- Canary still has not been run; `.runtime/canary_status.json` is empty.

## Recently landed

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

- `warp_repair.py` and the texture-warp branch — `enable_deep_texture` forced off.
- `_build_hand_preserve_mask` and the hand recomposite block — `preserve_hands` forced off.
- `validate_video_output` in `services/quality_contracts.py` — reads contract keys that
  no contract defines, so it raises KeyError on any call. Zero callers.

Decide to revive or delete these; leaving them is fine, leaving them *undocumented* is
what caused the audit findings.

## Notes for the next session

- Regenerate `pnpm-lock.yaml` (see above).
- Run the canary — it is the only end-to-end check nobody has exercised recently.
- ~100 definitions still meet a docstring trigger, mostly in `app.py`'s UI layer and
  the worker's provider plumbing. `docs/CODE_COMMENT_STANDARD.md` has the script that
  ranks them.
- `studio_tools/templates/worker_control.html` has 372 lines of JavaScript and no
  comments, against a well-commented `index.html` next door.
