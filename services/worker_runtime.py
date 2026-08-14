"""The worker's local status file and event log under `.runtime/`.

This is how the worker process tells the app process what it is doing — there is no
IPC between them, so the app reads these files to render Worker Control and the ops
banner. Atlas remains the source of truth for job state; these files describe only
this machine and are safe to delete (they are rebuilt on the next loop).

The event log is append-only NDJSON and is never rotated here, so it grows without
bound; `read_recent_worker_events` reads the whole file to return the tail.
"""

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
    """Return the worker's last published status, or a stopped-looking default.

    A missing or unparseable file yields workerRunning=False with null fields rather
    than raising, so the UI degrades to "stopped" instead of erroring. Note that this
    reports what the worker last *wrote*: a worker killed hard leaves its final status
    behind, which is why the app cross-checks liveness with the launchd service state.
    """
    path = get_worker_status_path(app_root)
    if not path.exists():
        return {
            "workerRunning": False,
            "currentJobId": None,
            "lastHeartbeatAt": None,
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
