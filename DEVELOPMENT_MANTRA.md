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
- face swap
- hold product
- image to video
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
