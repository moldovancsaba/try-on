from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_paths import get_app_root


def get_worker_runtime_dir(app_root: Path | None = None) -> Path:
    root = app_root or get_app_root()
    path = root / ".runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_worker_status_path(app_root: Path | None = None) -> Path:
    return get_worker_runtime_dir(app_root) / "worker_status.json"


def get_worker_events_path(app_root: Path | None = None) -> Path:
    return get_worker_runtime_dir(app_root) / "worker_events.ndjson"


def write_worker_status(payload: dict[str, Any], app_root: Path | None = None) -> Path:
    path = get_worker_status_path(app_root)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_worker_status(app_root: Path | None = None) -> dict[str, Any]:
    path = get_worker_status_path(app_root)
    if not path.exists():
        return {
            "workerRunning": False,
            "currentJobId": None,
            "lastLoopAt": None,
            "lastClaimedJobId": None,
            "lastSuccessAt": None,
            "lastFailureAt": None,
            "lastFailureCode": None,
            "lastFailureMessage": None,
            "pollIntervalSeconds": None,
            "enabled": None,
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "workerRunning": False,
            "currentJobId": None,
            "lastLoopAt": None,
            "lastClaimedJobId": None,
            "lastSuccessAt": None,
            "lastFailureAt": None,
            "lastFailureCode": None,
            "lastFailureMessage": None,
            "pollIntervalSeconds": None,
            "enabled": None,
        }


def append_worker_event(payload: dict[str, Any], app_root: Path | None = None) -> Path:
    path = get_worker_events_path(app_root)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    return path


def read_recent_worker_events(limit: int = 50, app_root: Path | None = None) -> list[dict[str, Any]]:
    path = get_worker_events_path(app_root)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    events: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        try:
            events.append(json.loads(raw))
        except Exception:
            continue
    return events
