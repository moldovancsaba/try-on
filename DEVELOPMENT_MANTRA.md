# Try-On Studio Development Mantra

This file defines the maintenance rules for the standalone app shipped in this repository.

## 1. Runtime Contract Comes First

Any change to model paths, startup flow, required checkpoints, device behavior, or mounted routes must be reflected in:

- `README.md`
- path helpers such as `model_paths.py`
- installer or audit scripts when the change affects provisioning

If the docs and runtime disagree, the runtime contract is considered broken until both are reconciled.

## 2. Shared Model Vault Is Canonical

The app is built around a centralized model store, not per-project checkpoints.

Rules:

- default model root is `/Users/Shared/Models`
- override with `TRYON_MODELS_ROOT`
- app settings do not belong in the model vault
- new model families must land in the shared vault under a stable namespace
- update the audit tooling and manifest expectations when the vault layout changes

## 3. Shipped Surface Must Stay Explicit

The root documentation should describe only what this repository actually ships:

- landing page
- try-on
- MotoGP leather-suit workflow
- worker control
- garment setup and library
- public API endpoints

Do not paste large upstream readmes into the root app documentation. Link to upstream instead.

## 4. Launcher Output Should Stay Useful

The current launcher filters a few known noisy lines for readability. That is an implementation detail, not a license to hide failures.

Rules:

- known startup noise may be filtered in `run.sh`
- real errors should be fixed at the source when practical
- do not rely on shell filtering to conceal broken behavior

## 5. Quality Mode Is the Baseline

This standalone build ships one supported try-on quality lane:

- `High Quality`

`Fast (Draft)` is not part of the supported product. If a speed mode is reintroduced, it must be documented as a shipped feature and validated against the current baseline.

## 6. Optional Features Must Declare Their Dependencies

Some pages rely on models outside the minimal installer seed set.

When adding or changing optional features:

- document whether the installer pre-seeds the models
- document whether first-use downloads are expected
- document which shared-vault directories must already exist

## 7. Keep the App-Centric Architecture Understandable

The repo is already a single-application stack with Gradio mounted into FastAPI. Keep the architecture legible.

Rules:

- avoid introducing new subsystems without documenting the resulting surface area
- prefer explicit route and storage contracts
- keep high-value operational behavior easy to trace from the root docs

## 8. Dedicated Worker Runtime Is Single-Task

The Camera queue worker is intended to run on a dedicated local machine that processes exactly one try-on job at a time.

Rules:

- keep `README.md` aligned with app, worker, queue, and launchd behavior
- keep service process names explicit through `tryon-app-server` and `tryon-queue-worker`
- do not add a second queue consumer, background generation path, or parallel try-on code path without a deliberate contract change
- keep readiness checks in front of queue claiming so the worker does not lease online work before the app is ready
- keep lock behavior easy to audit from `services/single_task_lock.py`, `app.py`, and `scripts/tryon_queue_worker.py`
