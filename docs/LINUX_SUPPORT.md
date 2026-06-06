# Linux Support

## Support level

Linux is supported as an experimental runtime target for local development and smoke validation. The primary production target remains the configured local processing machine.

Windows is out of scope for this repository.

## Supported Linux targets

- Python 3.11
- CPU runtime for contract and API smoke tests
- CUDA runtime when compatible PyTorch, drivers, and model assets are installed

Apple-specific service management remains macOS-only. Linux operators should run the app and worker manually or through their own process supervisor.

## Required validation path

Run project-owned tests without writing bytecode:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv311/bin/python -m unittest discover tests
```

Run a capability check after configuring the model vault:

```bash
./.venv311/bin/python scripts/audit_models.py --write-manifest
```

Start the app and verify:

- the local capabilities endpoint returns the feature matrix
- the try-on feature is either `ready` or reports deterministic missing assets
- unsupported optional features remain unavailable instead of appearing enabled

## Boundaries

- `launchd` service files are macOS-only.
- CUDA support depends on the host driver and PyTorch wheel.
- Optional feature assets are not part of the core installer contract unless explicitly listed in the capability matrix.
