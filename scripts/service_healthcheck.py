#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.service_manager import get_managed_services_status


def main() -> int:
    app_url = "http://127.0.0.1:7860/api/capabilities"
    services = get_managed_services_status(app_root=REPO_ROOT)
    payload: dict[str, object] = {
        "services": services,
        "appApiReachable": False,
        "appCapabilities": None,
        "errors": [],
    }

    try:
        response = requests.get(app_url, timeout=10)
        response.raise_for_status()
        payload["appApiReachable"] = True
        payload["appCapabilities"] = response.json()
    except Exception as error:  # pragma: no cover - operational probe
        payload["errors"].append(str(error))

    print(json.dumps(payload, indent=2))
    app_running = bool(services.get("app", {}).get("running"))
    worker_running = bool(services.get("worker", {}).get("running"))
    return 0 if app_running and worker_running and payload["appApiReachable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
