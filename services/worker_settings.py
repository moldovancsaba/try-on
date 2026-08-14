"""Operator-controlled worker settings, stored in `.config/worker_settings.json`.

This file — not the environment — is where the worker's enabled flag and poll interval
live, so an operator can pause the queue or slow polling from the Worker Control page
without editing files or restarting anything. The worker re-reads it each loop, so a
change takes effect within one poll interval and never interrupts a running job.

There is a `TRYON_POLL_INTERVAL_SECONDS` in the env example; it is not read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_paths import ensure_app_config_dir, get_app_root


DEFAULT_POLL_INTERVAL_SECONDS = 60
ALLOWED_POLL_INTERVAL_SECONDS = (60, 120, 180, 240, 300)
DEFAULT_WORKER_SETTINGS = {
    "enabled": True,
    "pollIntervalSeconds": DEFAULT_POLL_INTERVAL_SECONDS,
    "updatedAt": None,
    "updatedBy": None,
}


def get_worker_settings_path(app_root: Path | None = None) -> Path:
    root = app_root or get_app_root()
    return root / ".config" / "worker_settings.json"


def normalize_worker_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Return `raw` merged over the defaults, with unusable values replaced.

    The poll interval is restricted to ALLOWED_POLL_INTERVAL_SECONDS rather than
    clamped to a range: this is a UI dropdown, and an arbitrary value from a direct
    API call falls back to the 60s default instead of being honoured. That also caps
    how fast a misconfiguration can hammer Atlas.

    Never raises — a corrupt settings payload degrades to defaults, because the worker
    must keep running with sane settings rather than fail to start.
    """
    data = dict(DEFAULT_WORKER_SETTINGS)
    if raw:
        data.update(raw)
    enabled = bool(data.get("enabled", True))
    interval = data.get("pollIntervalSeconds", DEFAULT_POLL_INTERVAL_SECONDS)
    try:
        interval = int(interval)
    except Exception:
        interval = DEFAULT_POLL_INTERVAL_SECONDS
    if interval not in ALLOWED_POLL_INTERVAL_SECONDS:
        interval = DEFAULT_POLL_INTERVAL_SECONDS
    return {
        "enabled": enabled,
        "pollIntervalSeconds": interval,
        "updatedAt": data.get("updatedAt"),
        "updatedBy": data.get("updatedBy"),
    }


def load_worker_settings(app_root: Path | None = None) -> dict[str, Any]:
    path = get_worker_settings_path(app_root)
    if not path.exists():
        return dict(DEFAULT_WORKER_SETTINGS)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_WORKER_SETTINGS)
    return normalize_worker_settings(payload)


def save_worker_settings(settings: dict[str, Any], app_root: Path | None = None) -> Path:
    path = get_worker_settings_path(app_root)
    ensure_app_config_dir(app_root or get_app_root())
    normalized = normalize_worker_settings(settings)
    path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    return path
