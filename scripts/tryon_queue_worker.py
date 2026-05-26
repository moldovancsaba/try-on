#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from pymongo import MongoClient, ReturnDocument


UTC = timezone.utc


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def plus_seconds(iso_value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def plus_minutes(iso_value: str, minutes: int) -> str:
    return plus_seconds(iso_value, minutes * 60)


def parse_int(value: str | None, fallback: int) -> int:
    try:
        parsed = int((value or "").strip())
        return parsed if parsed > 0 else fallback
    except Exception:
        return fallback


def parse_list(value: str | None, fallback: list[str]) -> list[str]:
    if not value or not value.strip():
        return fallback
    return [item.strip().lower() for item in value.split(",") if item.strip()]


@dataclass
class WorkerConfig:
    mongodb_uri: str
    mongodb_db_name: str
    queue_root: Path
    worker_id: str
    poll_interval_seconds: int
    lease_duration_seconds: int
    max_attempts: int
    allowed_source_hosts: list[str]
    suit_asset_root: Path | None
    local_tryon_api_url: str
    local_tryon_timeout_seconds: int
    imgbb_api_key: str
    camera_complete_url: str
    camera_internal_secret: str


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
        os.environ.setdefault(key, value)


def load_config() -> WorkerConfig:
    repo_root = Path(__file__).resolve().parent.parent
    load_env_file(repo_root / ".env.tryon-worker")
    load_env_file(repo_root / ".env.local")

    mongodb_uri = (os.getenv("MONGODB_ATLAS_URI") or os.getenv("MONGODB_URI") or "").strip()
    mongodb_db_name = (os.getenv("MONGODB_DB_NAME") or os.getenv("MONGODB_DB") or "").strip()
    if not mongodb_uri:
        raise RuntimeError("MONGODB_ATLAS_URI or MONGODB_URI is required")
    if not mongodb_db_name:
        raise RuntimeError("MONGODB_DB_NAME or MONGODB_DB is required")

    camera_complete_url = (os.getenv("CAMERA_TRYON_COMPLETE_URL") or "").strip()
    camera_internal_secret = (os.getenv("CAMERA_TRYON_INTERNAL_SECRET") or "").strip()
    imgbb_api_key = (os.getenv("IMGBB_API_KEY") or "").strip()
    if not camera_complete_url:
        raise RuntimeError("CAMERA_TRYON_COMPLETE_URL is required")
    if not camera_internal_secret:
        raise RuntimeError("CAMERA_TRYON_INTERNAL_SECRET is required")
    if not imgbb_api_key:
        raise RuntimeError("IMGBB_API_KEY is required")

    return WorkerConfig(
        mongodb_uri=mongodb_uri,
        mongodb_db_name=mongodb_db_name,
        queue_root=Path((os.getenv("TRYON_QUEUE_ROOT") or "/Users/Shared/Projects/try-on/queue").strip()).expanduser(),
        worker_id=(os.getenv("TRYON_WORKER_ID") or socket.gethostname() or "tryon-worker-01").strip(),
        poll_interval_seconds=parse_int(os.getenv("TRYON_POLL_INTERVAL_SECONDS"), 20),
        lease_duration_seconds=parse_int(os.getenv("TRYON_LEASE_DURATION_SECONDS"), 600),
        max_attempts=parse_int(os.getenv("TRYON_MAX_ATTEMPTS"), 3),
        allowed_source_hosts=parse_list(os.getenv("TRYON_ALLOWED_SOURCE_HOSTS"), ["i.ibb.co"]),
        suit_asset_root=(
            Path((os.getenv("TRYON_SUIT_ASSET_ROOT") or "").strip()).expanduser()
            if (os.getenv("TRYON_SUIT_ASSET_ROOT") or "").strip()
            else None
        ),
        local_tryon_api_url=(os.getenv("TRYON_LOCAL_API_URL") or "http://127.0.0.1:7860/api/tryon/run").strip(),
        local_tryon_timeout_seconds=parse_int(os.getenv("TRYON_LOCAL_API_TIMEOUT_SECONDS"), 900),
        imgbb_api_key=imgbb_api_key,
        camera_complete_url=camera_complete_url,
        camera_internal_secret=camera_internal_secret,
    )


def ensure_queue_dirs(queue_root: Path) -> None:
    for name in ("incoming", "processing", "done", "failed", "logs"):
        (queue_root / name).mkdir(parents=True, exist_ok=True)


def append_log(log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def classify_failure(message: str) -> tuple[bool, str]:
    lower = message.lower()
    if any(token in lower for token in ("timeout", "temporarily", "connection", "fetch failed", "503", "502")):
        return True, "transient_runtime_error"
    if "allowlisted" in lower or "oversized" in lower:
        return False, "invalid_source_image"
    if "missing suit" in lower or "invalid suit" in lower:
        return False, "invalid_suit"
    return False, "processing_failed"


def retry_delay_minutes(attempt_count: int) -> int | None:
    if attempt_count <= 1:
        return 5
    if attempt_count == 2:
        return 30
    return None


class TryOnQueueWorker:
    def __init__(self, config: WorkerConfig):
        self.config = config
        self.mongo = MongoClient(config.mongodb_uri)
        self.db = self.mongo[config.mongodb_db_name]
        self.jobs = self.db["tryon_jobs"]
        self.suits = self.db["leather_suits"]

    def recover_stale_jobs(self) -> int:
        now = now_iso()
        result = self.jobs.update_many(
            {
                "status": {"$in": ["claimed", "processing", "uploading_result"]},
                "processing.leaseExpiresAt": {"$lt": now},
            },
            {
                "$set": {
                    "status": "retry_wait",
                    "stage": "failed",
                    "updatedAt": now,
                    "processing.nextAttemptAt": plus_minutes(now, 5),
                    "processing.leaseExpiresAt": None,
                }
            },
        )
        return int(result.modified_count)

    def claim_next_job(self) -> dict[str, Any] | None:
        now = now_iso()
        return self.jobs.find_one_and_update(
            {
                "status": {"$in": ["queued", "retry_wait"]},
                "processing.nextAttemptAt": {"$lte": now},
                "$or": [
                    {"processing.leaseExpiresAt": None},
                    {"processing.leaseExpiresAt": {"$lt": now}},
                ],
            },
            {
                "$set": {
                    "status": "claimed",
                    "stage": "claimed",
                    "updatedAt": now,
                    "processing.workerId": self.config.worker_id,
                    "processing.claimedAt": now,
                    "processing.leaseExpiresAt": plus_seconds(now, self.config.lease_duration_seconds),
                },
                "$inc": {"processing.attemptCount": 1},
            },
            sort=[("createdAt", 1)],
            return_document=ReturnDocument.AFTER,
        )

    def update_stage(self, job_id: str, status: str, stage: str, patch: dict[str, Any] | None = None) -> None:
        payload = {"status": status, "stage": stage, "updatedAt": now_iso()}
        if patch:
            payload.update(patch)
        self.jobs.update_one({"jobId": job_id}, {"$set": payload})

    def heartbeat(self, job_id: str) -> None:
        now = now_iso()
        self.jobs.update_one(
            {"jobId": job_id},
            {
                "$set": {
                    "processing.lastHeartbeatAt": now,
                    "processing.leaseExpiresAt": plus_seconds(now, self.config.lease_duration_seconds),
                    "updatedAt": now,
                }
            },
        )

    def schedule_retry_or_failure(self, job: dict[str, Any], code: str, message: str, details: str | None = None) -> str:
        attempt_count = int(job.get("processing", {}).get("attemptCount", 0))
        delay_minutes = retry_delay_minutes(attempt_count) if attempt_count < self.config.max_attempts else None
        now = now_iso()
        payload = {
            "error": {"code": code, "message": message, "details": details},
            "updatedAt": now,
            "processing.leaseExpiresAt": None,
        }
        if delay_minutes is not None:
            payload["status"] = "retry_wait"
            payload["stage"] = "failed"
            payload["processing.nextAttemptAt"] = plus_minutes(now, delay_minutes)
            self.jobs.update_one({"jobId": job["jobId"]}, {"$set": payload})
            return "retry_wait"
        payload["status"] = "failed"
        payload["stage"] = "failed"
        payload["processing.finishedAt"] = now
        self.jobs.update_one({"jobId": job["jobId"]}, {"$set": payload})
        return "failed"

    def resolve_local_suit_asset(self, suit: dict[str, Any]) -> Path:
        if not self.config.suit_asset_root:
            raise RuntimeError("missing legacy local suit asset root")

        root = self.config.suit_asset_root
        candidates: list[Path] = []
        relative_path = suit.get("assetRelativePath")
        if isinstance(relative_path, str) and relative_path.strip():
            candidates.append(root / relative_path)

        asset_key = str(suit.get("assetKey") or "").strip()
        if asset_key:
            candidates.extend(
                [
                    root / asset_key,
                    root / f"{asset_key}.png",
                    root / f"{asset_key}.jpg",
                    root / f"{asset_key}.jpeg",
                    root / f"{asset_key}.webp",
                ]
            )

        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise RuntimeError(f"missing suit asset:{suit.get('leatherSuitId')}")

    def download_source_image(self, image_url: str, destination: Path) -> None:
        hostname = urlparse(image_url).hostname or ""
        if hostname.lower() not in self.config.allowed_source_hosts:
            raise RuntimeError(f"source_host_not_allowlisted:{hostname}")

        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        if len(response.content) > 25 * 1024 * 1024:
            raise RuntimeError(f"oversized source image:{len(response.content)}")
        destination.write_bytes(response.content)

    def stage_suit_asset(self, leather_suit_id: str, destination: Path) -> str:
        suit = self.suits.find_one({"leatherSuitId": leather_suit_id, "active": True})
        if not suit:
            raise RuntimeError(f"missing suit:{leather_suit_id}")

        remote_url = (
            str(suit.get("sourceImageUrl") or "").strip()
            or str(suit.get("imageUrl") or "").strip()
            or str(suit.get("previewUrl") or "").strip()
        )
        if remote_url:
            self.download_source_image(remote_url, destination)
            return remote_url

        local_asset = self.resolve_local_suit_asset(suit)
        shutil.copyfile(local_asset, destination)
        return str(local_asset)

    def call_local_tryon_api(self, person_input_path: Path, suit_input_path: Path, output_path: Path) -> dict[str, Any]:
        payload = {
            "person_image_path": str(person_input_path),
            "garment_image_path": str(suit_input_path),
            "output_image_path": str(output_path),
        }
        response = requests.post(
            self.config.local_tryon_api_url,
            json=payload,
            timeout=self.config.local_tryon_timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"local_tryon_api_failed:{response.status_code}:{response.text[:300]}")
        data = response.json()
        if not output_path.exists():
            raise RuntimeError("local_tryon_api_missing_output")
        return data

    def upload_to_imgbb(self, image_path: Path) -> dict[str, Any]:
        encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        response = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": self.config.imgbb_api_key, "image": encoded},
            timeout=120,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"imgbb_upload_failed:{response.status_code}:{response.text[:300]}")
        payload = response.json()
        data = payload.get("data") or {}
        image_url = data.get("url")
        if not image_url:
            raise RuntimeError("imgbb_upload_missing_url")
        return {"imageUrl": image_url, "deleteUrl": data.get("delete_url")}

    def notify_camera_completion(self, job_id: str, upload: dict[str, Any]) -> None:
        response = requests.post(
            self.config.camera_complete_url,
            json={
                "jobId": job_id,
                "publicResultUrl": upload["imageUrl"],
                "deleteUrl": upload.get("deleteUrl"),
                "workerId": self.config.worker_id,
                "processorMeta": {"pipelineVersion": "1.0.0"},
            },
            headers={"x-camera-tryon-secret": self.config.camera_internal_secret},
            timeout=60,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"camera_completion_failed:{response.status_code}:{response.text[:300]}")

    def process_job(self, job: dict[str, Any]) -> None:
        job_id = job["jobId"]
        workspace_root = self.config.queue_root / "processing" / job_id
        workspace_root.mkdir(parents=True, exist_ok=True)
        person_input_path = workspace_root / "person_input.jpg"
        suit_input_path = workspace_root / "suit_input.png"
        result_path = workspace_root / "result.png"
        metadata_path = workspace_root / "metadata.json"
        log_path = workspace_root / "log.txt"

        stop_heartbeat = threading.Event()

        def _heartbeat_loop() -> None:
            while not stop_heartbeat.wait(max(5, int(self.config.lease_duration_seconds * 0.4))):
                self.heartbeat(job_id)

        heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        heartbeat_thread.start()

        try:
            self.update_stage(job_id, "processing", "downloading_input", {"processing.startedAt": now_iso()})
            self.download_source_image(job["source"]["imageUrl"], person_input_path)

            self.update_stage(job_id, "processing", "resolving_suit")
            resolved_suit_source = self.stage_suit_asset(job["request"]["leatherSuitId"], suit_input_path)

            metadata_path.write_text(
                json.dumps(
                    {
                        "jobId": job_id,
                        "submissionId": job["source"]["submissionId"],
                        "leatherSuitId": job["request"]["leatherSuitId"],
                        "workerId": self.config.worker_id,
                        "sourceImageUrl": job["source"]["imageUrl"],
                        "resolvedSuitAssetPath": resolved_suit_source,
                        "createdAt": now_iso(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            self.update_stage(job_id, "processing", "running_tryon")
            api_result = self.call_local_tryon_api(person_input_path, suit_input_path, result_path)
            append_log(log_path, json.dumps({"stage": "running_tryon", "response": api_result}))

            self.update_stage(job_id, "uploading_result", "uploading_result")
            upload = self.upload_to_imgbb(result_path)
            self.notify_camera_completion(job_id, upload)

            done_payload = {
                "status": "done",
                "stage": "done",
                "updatedAt": now_iso(),
                "processing.finishedAt": now_iso(),
                "processing.leaseExpiresAt": None,
                "processing.lastHeartbeatAt": now_iso(),
                "result": {
                    "publicResultUrl": upload["imageUrl"],
                    "imgbbDeleteUrl": upload.get("deleteUrl"),
                    "provider": "imgbb",
                },
                "error": {"code": None, "message": None, "details": None},
            }
            self.jobs.update_one({"jobId": job_id}, {"$set": done_payload})
            target = self.config.queue_root / "done" / job_id
            target.parent.mkdir(parents=True, exist_ok=True)
            if workspace_root.exists():
                if target.exists():
                    shutil.rmtree(target)
                shutil.move(str(workspace_root), str(target))
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            transient, code = classify_failure(message)
            latest_job = self.jobs.find_one({"jobId": job_id}) or job
            outcome = self.schedule_retry_or_failure(latest_job, code, message)
            append_log(log_path, json.dumps({"stage": outcome, "error": message}))
            target = self.config.queue_root / "failed" / job_id
            target.parent.mkdir(parents=True, exist_ok=True)
            if workspace_root.exists():
                if target.exists():
                    shutil.rmtree(target)
                shutil.move(str(workspace_root), str(target))
            if not transient and outcome == "failed":
                print(f"[tryon-worker] permanent failure {job_id}: {message}", file=sys.stderr)
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=2)

    def run_once(self) -> bool:
        self.recover_stale_jobs()
        job = self.claim_next_job()
        if not job:
            print("[tryon-worker] no claimable jobs found")
            return False
        print(f"[tryon-worker] claimed job {job['jobId']}")
        self.process_job(job)
        return True

    def run_forever(self) -> None:
        ensure_queue_dirs(self.config.queue_root)
        while True:
            self.run_once()
            time.sleep(self.config.poll_interval_seconds)


def main() -> int:
    config = load_config()
    ensure_queue_dirs(config.queue_root)
    worker = TryOnQueueWorker(config)
    run_once = "--once" in sys.argv
    if run_once:
        worker.run_once()
        return 0
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
