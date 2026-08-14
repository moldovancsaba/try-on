#!/usr/bin/env python3
"""End-to-end canary: prove the local stack can still produce a render.

Exercises the real path rather than pinging a health endpoint, and writes the outcome
to .runtime/canary_status.json so an operator can see when the stack was last known
good. Worth running after a model vault change or a long idle period, since both are
how the stack breaks silently.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.service_healthcheck import main as service_health_main
from services.worker_infra import CANARY_SCHEMA_VERSION, now_iso
from services.worker_runtime import get_worker_runtime_dir


def main() -> int:
    status = "passed"
    exit_code = 0
    try:
        exit_code = service_health_main()
        if exit_code != 0:
            status = "failed"
    except Exception as exc:  # pragma: no cover - operational CLI
        status = "failed"
        exit_code = 1
        error = str(exc)
    else:
        error = None
    payload = {"schemaVersion": CANARY_SCHEMA_VERSION, "checkedAt": now_iso(), "status": status, "error": error}
    path = get_worker_runtime_dir(REPO_ROOT) / "canary_status.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
