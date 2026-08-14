#!/usr/bin/env python3
"""Pre-flight check for the worker contract before running live jobs.

Verifies the things a worker needs and cannot recover from at runtime: required
environment variables, Atlas reachability, the queue directories, and the local
try-on API. Run it after editing `.env.tryon-worker` and before letting the worker
claim real Camera jobs, since a misconfigured worker leases jobs it cannot finish.

Checks configuration only — it does not render anything. Use tryon_canary.py for that.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests
from pymongo import MongoClient


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        if not os.environ.get(key):
            os.environ[key] = value


def print_result(ok: bool, label: str, detail: str) -> bool:
    prefix = "✓" if ok else "✗"
    print(f"{prefix} {label}: {detail}")
    return ok


def check_env(name: str, aliases: list[str] | None = None) -> tuple[bool, str]:
    keys = [name, *(aliases or [])]
    for key in keys:
        value = (os.getenv(key) or "").strip()
        if value:
            return True, value
    return False, ""


def test_mongodb(uri: str, db_name: str) -> bool:
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command({"ping": 1})
        db = client[db_name]
        db["tryon_jobs"].estimated_document_count()
        db["leather_suits"].estimated_document_count()
        db[os.getenv("TRYON_SETUP_COLLECTION") or "tryon_setups"].estimated_document_count()
        return print_result(True, "MongoDB Atlas", f"connected to database `{db_name}`")
    except Exception as error:  # pragma: no cover - operational check
        return print_result(False, "MongoDB Atlas", str(error))
    finally:
        client.close()


def test_imgbb(api_key: str) -> bool:
    probe_png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    try:
        response = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": api_key, "image": probe_png},
            timeout=30,
        )
        payload = response.json()
        if response.ok and payload.get("success"):
            return print_result(True, "ImgBB", "API key accepted")
        return print_result(False, "ImgBB", payload.get("error", {}).get("message", "invalid key"))
    except Exception as error:  # pragma: no cover - operational check
        return print_result(False, "ImgBB", str(error))


def test_url(name: str, url: str, headers: dict[str, str] | None = None) -> bool:
    try:
        response = requests.options(url, timeout=15, headers=headers or {})
        return print_result(
            response.status_code < 500,
            name,
            f"reachable (HTTP {response.status_code})",
        )
    except Exception as error:  # pragma: no cover - operational check
        return print_result(False, name, str(error))


def test_local_api(url: str) -> bool:
    try:
        response = requests.get(url.rsplit("/api/tryon/run", 1)[0] + "/api/capabilities", timeout=15)
        if response.ok:
            payload = response.json()
            return print_result(True, "Local try-on API", f"capabilities OK on {payload.get('device', 'unknown device')}")
        return print_result(False, "Local try-on API", f"HTTP {response.status_code}")
    except Exception as error:  # pragma: no cover - operational check
        return print_result(False, "Local try-on API", str(error))


def test_queue_dirs(root: Path) -> bool:
    required = ["incoming", "processing", "done", "failed", "logs"]
    missing = [name for name in required if not (root / name).exists()]
    if missing:
        return print_result(False, "Queue directories", f"missing: {', '.join(missing)}")
    return print_result(True, "Queue directories", str(root))


def test_suit_root(root: Path) -> bool:
    if not root.exists():
        return print_result(True, "Suit asset root", f"optional legacy fallback not configured at `{root}`")
    files = [path for path in root.rglob("*") if path.is_file()]
    if not files:
        return print_result(True, "Suit asset root", f"optional legacy fallback has no files under `{root}`")
    return print_result(True, "Suit asset root", f"{len(files)} legacy fallback files available")


def test_fal_optional() -> bool:
    fal_key = (os.getenv("FAL_KEY") or "").strip()
    fal_model = (os.getenv("FAL_TRYON_MODEL") or "fal-ai/fashn/tryon/v1.6").strip()
    if not fal_key:
        return print_result(True, "FAL setup", "not configured (jobs auto-fallback to segmind/local)")
    if not fal_model:
        return print_result(False, "FAL setup", "configured key present but model missing")
    return print_result(True, "FAL setup", f"configured model={fal_model}")


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    load_env_file(repo_root / ".env.tryon-worker")
    load_env_file(repo_root / ".env.local")

    print("Try-On worker setup verification\n")

    checks: list[bool] = []

    mongo_ok, mongo_uri = check_env("MONGODB_ATLAS_URI", ["MONGODB_URI"])
    mongo_db_ok, mongo_db = check_env("MONGODB_DB_NAME", ["MONGODB_DB"])
    imgbb_ok, imgbb_key = check_env("IMGBB_API_KEY")
    complete_ok, complete_url = check_env("CAMERA_TRYON_COMPLETE_URL")
    secret_ok, _secret = check_env("CAMERA_TRYON_INTERNAL_SECRET")
    local_api_ok, local_api_url = check_env("TRYON_LOCAL_API_URL")
    person_hosts_ok, person_hosts = check_env("TRYON_ALLOWED_PERSON_SOURCE_HOSTS", ["TRYON_ALLOWED_SOURCE_HOSTS"])
    suit_hosts_ok, suit_hosts = check_env("TRYON_ALLOWED_SUIT_SOURCE_HOSTS", ["TRYON_ALLOWED_SOURCE_HOSTS"])

    checks.append(print_result(mongo_ok, "MONGODB_ATLAS_URI / MONGODB_URI", "configured" if mongo_ok else "missing"))
    checks.append(print_result(mongo_db_ok, "MONGODB_DB_NAME / MONGODB_DB", mongo_db if mongo_db_ok else "missing"))
    checks.append(print_result(imgbb_ok, "IMGBB_API_KEY", "configured" if imgbb_ok else "missing"))
    checks.append(print_result(complete_ok, "CAMERA_TRYON_COMPLETE_URL", complete_url if complete_ok else "missing"))
    checks.append(print_result(secret_ok, "CAMERA_TRYON_INTERNAL_SECRET", "configured" if secret_ok else "missing"))
    checks.append(print_result(local_api_ok, "TRYON_LOCAL_API_URL", local_api_url if local_api_ok else "missing"))
    checks.append(print_result(person_hosts_ok, "TRYON_ALLOWED_PERSON_SOURCE_HOSTS", person_hosts if person_hosts_ok else "missing"))
    checks.append(print_result(suit_hosts_ok, "TRYON_ALLOWED_SUIT_SOURCE_HOSTS", suit_hosts if suit_hosts_ok else "missing"))
    checks.append(test_fal_optional())

    queue_root = Path((os.getenv("TRYON_QUEUE_ROOT") or "/Users/Shared/Projects/try-on/queue").strip()).expanduser()
    suit_root = Path((os.getenv("TRYON_SUIT_ASSET_ROOT") or "/Users/Shared/Projects/try-on/images").strip()).expanduser()
    checks.append(test_queue_dirs(queue_root))
    checks.append(test_suit_root(suit_root))

    if mongo_ok and mongo_db_ok:
        checks.append(test_mongodb(mongo_uri, mongo_db))
    if imgbb_ok:
        checks.append(test_imgbb(imgbb_key))
    if complete_ok:
        checks.append(test_url("Camera completion endpoint", complete_url))
    if local_api_ok:
        checks.append(test_local_api(local_api_url))

    passed = sum(1 for item in checks if item)
    total = len(checks)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
