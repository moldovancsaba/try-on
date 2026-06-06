#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps
from pymongo import MongoClient, ReturnDocument, UpdateOne

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.worker_contracts import (
    PROCESSING_PROFILE_MOTOGP,
    PROCESSING_PROFILE_SEGMIND_IDM_VTON,
    PROCESSING_PROFILE_FAL_TRYON,
    normalize_job_document,
    normalize_processing_profile,
    normalize_suit_document,
    validate_job_document,
    validate_suit_document,
)
from services.mongo_uri import normalize_mongodb_uri
from services.single_task_lock import SingleTaskLock
from services.worker_runtime import append_worker_event, write_worker_status
from services.worker_settings import DEFAULT_POLL_INTERVAL_SECONDS, load_worker_settings


UTC = timezone.utc
PIPELINE_VERSION = "1.1.0"
FAL_SETUP_ID = "fal_ai_tryon"
PUBLICATION_STATE_UPLOADED = "uploaded"
PUBLICATION_STATE_CAMERA_NOTIFIED = "camera_notified"
PUBLICATION_STATE_NOT_STARTED = "not_started"
TRYON_SETUP_COLLECTION = "tryon_setups"
TRYON_SETUP_PREFERENCES_COLLECTION = "camera_setup_preferences"
TRYON_DEFAULT_SETUP_ID = "default_motogp"
from services.tryon_setups import TRYON_SETUP_FIELD_ALLOWLIST, load_local_setups

SEGMIND_RATIO_WIDTH = 3
SEGMIND_RATIO_HEIGHT = 4
SEGMIND_TARGET_RATIO = SEGMIND_RATIO_WIDTH / SEGMIND_RATIO_HEIGHT
SEGMIND_ALLOWED_CATEGORIES = {"upper_body", "lower_body", "dresses"}
FAL_FULL_BODY_PROMPT = (
    "Full-body motorcycle leather suit try-on with the model wearing exactly the provided garment. "
    "Preserve all logos, text, numerals, symbols, edges, seams, fabric textures, color, and placement without distortion. "
    "Do not repaint or recolor brand text. Keep face and body identity unchanged. "
    "If the garment has transparent pixels, treat alpha as a hard boundary and avoid filling, bleeding, or recoloring transparent regions."
)
FAL_FULL_BRAND_PROMPT = FAL_FULL_BODY_PROMPT
FAL_MAX_BRAND_PRESERVATION_SEED = 42
FAL_MODE_QUALITY = "quality"
FAL_OUTPUT_FORMAT = "png"
FAL_CATEGORY_ONE_PIECES = "one-pieces"
FAL_SETUP_ID_ALIASES = {
    FAL_SETUP_ID,
}


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

def parse_bool(value: str | None, fallback: bool = False) -> bool:
    if value is None:
        return fallback
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class WorkerConfig:
    mongodb_uri: str
    mongodb_db_name: str
    queue_root: Path
    worker_id: str
    poll_interval_seconds: int
    lease_duration_seconds: int
    max_attempts: int
    worker_enabled: bool
    allowed_person_source_hosts: list[str]
    allowed_suit_source_hosts: list[str]
    max_source_image_bytes: int
    max_suit_image_bytes: int
    allow_redirects: bool
    suit_asset_root: Path | None
    local_tryon_api_url: str
    local_tryon_timeout_seconds: int
    imgbb_api_key: str
    camera_complete_url: str
    camera_internal_secret: str
    segmind_api_url: str
    segmind_api_key: str
    segmind_api_timeout_seconds: int
    fal_base_url: str
    fal_key: str
    fal_tryon_model: str
    fal_tryon_timeout_seconds: int
    setup_collection: str
    setup_preference_collection: str
    default_setup_id: str
    setup_catalog_path: str


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


def load_config() -> WorkerConfig:
    repo_root = Path(__file__).resolve().parent.parent
    load_env_file(repo_root / ".env.tryon-worker")
    load_env_file(repo_root / ".env.local")
    worker_settings = load_worker_settings(app_root=repo_root)

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

    fal_base_url = (os.getenv("FAL_BASE_URL") or "https://fal.run").strip()
    fal_key = (os.getenv("FAL_KEY") or "").strip()
    fal_tryon_model = (os.getenv("FAL_TRYON_MODEL") or "fal-ai/fashn/tryon/v1.6").strip()
    if not fal_tryon_model:
        fal_tryon_model = "fal-ai/fashn/tryon/v1.6"

    return WorkerConfig(
        mongodb_uri=mongodb_uri,
        mongodb_db_name=mongodb_db_name,
        queue_root=Path((os.getenv("TRYON_QUEUE_ROOT") or "/Users/Shared/Projects/try-on/queue").strip()).expanduser(),
        worker_id=(os.getenv("TRYON_WORKER_ID") or socket.gethostname() or "tryon-worker-01").strip(),
        poll_interval_seconds=int(worker_settings.get("pollIntervalSeconds", DEFAULT_POLL_INTERVAL_SECONDS)),
        lease_duration_seconds=parse_int(os.getenv("TRYON_LEASE_DURATION_SECONDS"), 600),
        max_attempts=parse_int(os.getenv("TRYON_MAX_ATTEMPTS"), 3),
        worker_enabled=bool(worker_settings.get("enabled", True)),
        allowed_person_source_hosts=parse_list(
            os.getenv("TRYON_ALLOWED_PERSON_SOURCE_HOSTS") or os.getenv("TRYON_ALLOWED_SOURCE_HOSTS"),
            ["i.ibb.co"],
        ),
        allowed_suit_source_hosts=parse_list(
            os.getenv("TRYON_ALLOWED_SUIT_SOURCE_HOSTS") or os.getenv("TRYON_ALLOWED_SOURCE_HOSTS"),
            ["i.ibb.co"],
        ),
        max_source_image_bytes=parse_int(os.getenv("TRYON_MAX_SOURCE_IMAGE_BYTES"), 25 * 1024 * 1024),
        max_suit_image_bytes=parse_int(os.getenv("TRYON_MAX_SUIT_IMAGE_BYTES"), 25 * 1024 * 1024),
        allow_redirects=(os.getenv("TRYON_ALLOW_REDIRECTS") or "").strip().lower() in {"1", "true", "yes"},
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
        segmind_api_url=(os.getenv("SEGMIND_API_URL") or "https://api.segmind.com/v1/idm-vton").strip(),
        segmind_api_key=(os.getenv("SEGMIND_API_KEY") or "").strip(),
        segmind_api_timeout_seconds=parse_int(os.getenv("SEGMIND_API_TIMEOUT_SECONDS"), 120),
        fal_base_url=fal_base_url,
        fal_key=fal_key,
        fal_tryon_model=fal_tryon_model,
        fal_tryon_timeout_seconds=parse_int(os.getenv("FAL_TRYON_TIMEOUT_SECONDS"), 300),
        setup_collection=(os.getenv("TRYON_SETUP_COLLECTION") or TRYON_SETUP_COLLECTION).strip(),
        setup_preference_collection=(os.getenv("TRYON_CAMERA_SETUP_PREFERENCE_COLLECTION") or TRYON_SETUP_PREFERENCES_COLLECTION).strip(),
        default_setup_id=(os.getenv("TRYON_DEFAULT_SETUP_ID") or TRYON_DEFAULT_SETUP_ID).strip(),
        setup_catalog_path=(os.getenv("TRYON_SETUP_CATALOG_PATH") or "").strip(),
    )


def ensure_queue_dirs(queue_root: Path) -> None:
    for name in ("incoming", "processing", "done", "failed", "logs"):
        (queue_root / name).mkdir(parents=True, exist_ok=True)


def append_log(log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def redact_url(value: str | None) -> str | None:
    if not value:
        return value
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _coerce_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return parse_bool(str(value), fallback=fallback)


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _image_has_alpha_channel(image: Image.Image) -> bool:
    if "A" in image.getbands():
        return True
    return image.mode == "P" and image.info.get("transparency") is not None


def _image_as_rgba_with_alpha(image: Image.Image) -> Image.Image:
    if "A" in image.getbands():
        return image
    if image.mode == "P" and image.info.get("transparency") is not None:
        return image.convert("RGBA")
    return image


def _normalize_segmind_category(value: Any) -> str:
    normalized = _safe_str(value) or ""
    key = normalized.strip().lower()
    if key in {
        "full_body",
        "full-body",
        "full body",
        "fullbody",
        "full-body (suits, dresses, rompers)",
        "full-body (suits, dresses, dresses)",
        "dresses",
        "dresses_only",
    }:
        return "dresses"
    if key in {"lower", "lower_body", "lower body", "legs"}:
        return "lower_body"
    if key in {"upper", "upper_body", "upper body", "torso"}:
        return "upper_body"
    if key in SEGMIND_ALLOWED_CATEGORIES:
        return key
    return "upper_body"


def _has_alpha_channel(image_path: Path) -> bool:
    with Image.open(image_path) as image:
        return _image_has_alpha_channel(image)


def _normalize_segmind_aspect(image_path: Path) -> None:
    with Image.open(image_path) as image:
        image = ImageOps.exif_transpose(image)
        has_alpha = _image_has_alpha_channel(image)
        if has_alpha:
            image = _image_as_rgba_with_alpha(image)
        width, height = image.size
        if width <= 0 or height <= 0:
            raise RuntimeError(f"invalid_image_dimensions:{image_path.name}")

        current_ratio = width / height
        if abs(current_ratio - SEGMIND_TARGET_RATIO) < 0.01:
            return

        if current_ratio > SEGMIND_TARGET_RATIO:
            new_height = max(height, int(round(width / SEGMIND_TARGET_RATIO)))
            pad_total = max(0, new_height - height)
            pad_top = pad_total // 2
            pad_bottom = pad_total - pad_top
            left = right = 0
        else:
            new_width = max(width, int(round(height * SEGMIND_TARGET_RATIO)))
            pad_total = max(0, new_width - width)
            left = pad_total // 2
            right = pad_total - left
            pad_top = pad_bottom = 0

        if has_alpha:
            fill = (0, 0, 0, 0)
            if image.mode != "RGBA":
                image = image.convert("RGBA")
        else:
            fill = (255, 255, 255)
            if image.mode != "RGB":
                image = image.convert("RGB")

        padded = ImageOps.expand(image, border=(left, pad_top, right, pad_bottom), fill=fill)
        if has_alpha:
            padded.save(image_path, format="PNG")
        else:
            padded.save(image_path, format="PNG" if image_path.suffix.lower() == ".png" else "JPEG")


def _is_http_url(value: Any) -> bool:
    parsed = urlparse(_safe_str(value) or "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _strip_image_prefix(path: str | None) -> str | None:
    if not path:
        return None
    return path.strip()


def _looks_like_hex_submission_id(value: str | None) -> bool:
    candidate = (value or "").strip()
    return bool(re.fullmatch(r"[0-9a-fA-F]{24}", candidate))


def classify_failure(message: str) -> tuple[bool, str]:
    lower = message.lower()
    if any(
        token in lower
        for token in (
            "timeout",
            "temporarily",
            "connection",
            "fetch failed",
            "503",
            "502",
            "local_tryon_api_not_ready",
            "segmind_api_failed",
            "fal_request_id_missing",
            "fal_request_timeout",
            "fal_status_failed",
            "fal_output_missing",
            "fal_api_no_output",
            "imgbb_upload_failed",
        )
    ):
        return True, "transient_runtime_error"
    if any(token in lower for token in ("allowlisted", "oversized", "content_type_invalid", "redirect_blocked")):
        return False, "invalid_source_image"
    if "missing suit" in lower or "invalid suit" in lower:
        return False, "invalid_suit"
    if "source submission is invalid" in lower:
        return False, "invalid_source_submission"
    if "publicresulturl" in lower and "invalid" in lower:
        return False, "invalid_result_url"
    if "invalid_job_" in lower or "invalid_processing_profile" in lower or "unsupported_job_schema_version" in lower:
        return False, "invalid_job_contract"
    if "job_aborted_for_priority_push" in lower or "operator_aborted" in lower:
        return True, "transient_runtime_error"
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
        self.mongo = MongoClient(normalize_mongodb_uri(config.mongodb_uri))
        self.db = self.mongo[config.mongodb_db_name]
        self.jobs = self.db["tryon_jobs"]
        self.suits = self.db["leather_suits"]
        self.setups = self.db[config.setup_collection]
        self.camera_setup_preferences = self.db[config.setup_preference_collection]
        self.local_setups = load_local_setups(REPO_ROOT, catalog_path=config.setup_catalog_path)
        self.current_job_id: str | None = None
        self._fal_session_disabled = False
        self._fal_session_disable_reason: str | None = None
        self._sync_local_setups_to_mongo()
        self.ensure_default_setup_exists()

    def _normalize_setup_id(self, value: str | None) -> str | None:
        text = (value or "").strip()
        return text or None

    def _setup_sort_key(self, setup: dict[str, Any]) -> tuple[int, str]:
        return (int(setup.get("rank") or 0), str(setup.get("setupId") or ""))

    def _load_local_setup(self, setup_id: str | None) -> dict[str, Any] | None:
        setup = self.local_setups.get(self._normalize_setup_id(setup_id) or "")
        if not setup:
            return None
        if not bool(setup.get("active", True)):
            return None
        return setup

    def _load_setup_by_id(self, setup_id: str | None) -> dict[str, Any] | None:
        normalized = self._normalize_setup_id(setup_id)
        if not normalized:
            return None
        local_setup = self._load_local_setup(normalized)
        if local_setup:
            return local_setup

        setup = self.setups.find_one({"setupId": normalized, "active": True})
        if not setup:
            return None

        camera_id = setup.get("cameraId")
        return {
            "setupId": normalized,
            "name": str(setup.get("name") or normalized),
            "description": str(setup.get("description") or ""),
            "active": bool(setup.get("active", True)),
            "isDefault": bool(setup.get("isDefault")),
            "cameraId": self._normalize_setup_id(str(camera_id)) if camera_id not in (None, "") else None,
            "rank": int(setup.get("rank") or 0),
            "revision": self._normalize_setup_id(str(setup.get("revision") or "")),
            "config": {},
        }

    def _load_local_default_setup(self, camera_id: str | None, *, allow_camera: bool) -> dict[str, Any] | None:
        normalized_camera = self._normalize_setup_id(camera_id) if allow_camera else None
        candidates = [
            setup
            for setup in self.local_setups.values()
            if bool(setup.get("active", True))
            and bool(setup.get("isDefault"))
            and (
                (allow_camera and self._normalize_setup_id(str(setup.get("cameraId") or "")) == normalized_camera)
                or (not allow_camera and not self._normalize_setup_id(str(setup.get("cameraId") or "")))
            )
        ]
        if not candidates:
            return None
        return sorted(candidates, key=self._setup_sort_key)[0]

    def _sync_local_setups_to_mongo(self) -> None:
        now = now_iso()
        operations: list[UpdateOne] = []

        for setup in self.local_setups.values():
            setup_id = self._normalize_setup_id(setup.get("setupId"))
            if not setup_id or not bool(setup.get("active", True)):
                continue

            payload = {
                "name": str(setup.get("name") or setup_id),
                "description": self._normalize_setup_id(str(setup.get("description") or "")),
                "cameraId": self._normalize_setup_id(str(setup.get("cameraId") or "")),
                "active": True,
                "isDefault": bool(setup.get("isDefault")),
                "rank": int(setup.get("rank") or 0),
                "updatedAt": now,
                "provider": str(setup.get("provider") or "online"),
            }
            payload["description"] = payload["description"] or None
            revision = self._normalize_setup_id(str(setup.get("revision") or ""))
            if revision:
                payload["revision"] = revision
            if setup_id == FAL_SETUP_ID:
                payload["config"] = {key: setup.get("config", {}).get(key) for key in setup.get("config", {}) if key in TRYON_SETUP_FIELD_ALLOWLIST}
                payload["config"]["processing_profile"] = PROCESSING_PROFILE_FAL_TRYON
                payload["config"]["steps"] = payload["config"].get("steps", 32)
                payload["config"]["seed"] = payload["config"].get("seed", FAL_MAX_BRAND_PRESERVATION_SEED)
                payload["config"]["category"] = payload["config"].get("category", "dresses")
                if not payload["config"].get("garment_des"):
                    payload["config"]["garment_des"] = FAL_FULL_BODY_PROMPT

            operations.append(
                UpdateOne(
                    {"setupId": setup_id},
                    {
                        "$set": payload,
                        "$setOnInsert": {"createdAt": now},
                    },
                    upsert=True,
                )
            )

        if operations:
            self.setups.bulk_write(operations, ordered=False)

    def _load_last_camera_setup(self, camera_id: str | None) -> dict[str, Any] | None:
        normalized = self._normalize_setup_id(camera_id)
        if not normalized:
            return None
        preference = self.camera_setup_preferences.find_one({"cameraId": normalized})
        setup_id = self._normalize_setup_id(preference.get("setupId") if preference else None)
        if setup_id:
            setup = self._load_setup_by_id(setup_id)
            if setup:
                return setup

        local_camera_default = self._load_local_default_setup(normalized, allow_camera=True)
        if local_camera_default:
            return local_camera_default

        return self.setups.find_one(
            {"cameraId": normalized, "active": True, "isDefault": True},
            sort=[("rank", 1)],
        )

    def _load_global_default_setup(self) -> dict[str, Any] | None:
        local_default = self._load_local_default_setup(None, allow_camera=False)
        if local_default:
            return local_default
        return self.setups.find_one(
            {
                "active": True,
                "isDefault": True,
                "$or": [{"cameraId": {"$exists": False}}, {"cameraId": None}],
            },
            sort=[("rank", 1)],
        )

    def _mark_camera_last_setup(self, camera_id: str | None, setup_id: str) -> None:
        normalized = self._normalize_setup_id(camera_id)
        normalized_setup = self._normalize_setup_id(setup_id)
        if not normalized or not normalized_setup:
            return
        self.camera_setup_preferences.update_one(
            {"cameraId": normalized},
            {"$set": {"cameraId": normalized, "setupId": normalized_setup, "updatedAt": now_iso()}},
            upsert=True,
        )

    def _coerce_setup_metadata(self, setup: dict[str, Any]) -> dict[str, Any]:
        setup_id = self._normalize_setup_id(setup.get("setupId")) or "legacy_fallback"
        config = setup.get("config") if isinstance(setup.get("config"), dict) else {}
        payload = {
            "person_image_path": None,
            "garment_image_path": None,
            "output_image_path": None,
            "processing_profile": PROCESSING_PROFILE_MOTOGP,
        }
        for key in TRYON_SETUP_FIELD_ALLOWLIST:
            if key in config:
                payload[key] = config[key]

        resolved_profile = str(payload["processing_profile"]).strip() or PROCESSING_PROFILE_MOTOGP
        if setup_id in FAL_SETUP_ID_ALIASES or resolved_profile == PROCESSING_PROFILE_FAL_TRYON:
            payload["processing_profile"] = PROCESSING_PROFILE_FAL_TRYON
            payload["category"] = str(payload.get("category") or "dresses")
            payload["steps"] = _coerce_int(str(config.get("steps")), 32)
            payload["seed"] = _coerce_int(str(config.get("seed")), 42)
            payload["garment_des"] = _safe_str(config.get("garment_des")) or FAL_FULL_BODY_PROMPT
            resolved_profile = PROCESSING_PROFILE_FAL_TRYON
        else:
            payload["processing_profile"] = resolved_profile

        return {
            "setupId": setup_id,
            "name": str(setup.get("name") or setup_id),
            "revision": setup.get("revision"),
            "config": config,
            "payload": payload,
            "isDefault": bool(setup.get("isDefault")),
        }

    def resolve_setup(self, job: dict[str, Any]) -> tuple[dict[str, Any], str]:
        request = job.get("request") or {}
        source = job.get("source") or {}
        camera_id = self._normalize_setup_id(str(source.get("cameraId") or request.get("cameraId") or ""))

        requested_setup_id = self._normalize_setup_id(str(request.get("setupId") or ""))
        if requested_setup_id:
            requested_setup = self._load_setup_by_id(requested_setup_id)
            if requested_setup:
                self._mark_camera_last_setup(camera_id, requested_setup_id)
                return self._coerce_setup_metadata(requested_setup), "job.assigned"
            self.emit_event(
                level="warn",
                event="invalid_setup_reference",
                status="processing",
                stage="normalizing_job",
                details={"requestedSetupId": requested_setup_id},
            )

        if camera_id:
            camera_setup = self._load_last_camera_setup(camera_id)
            if camera_setup:
                return self._coerce_setup_metadata(camera_setup), "camera.last"

        default_setup = self._load_global_default_setup()
        if default_setup:
            return self._coerce_setup_metadata(default_setup), "global.default"

        # Compatibility fallback for older jobs without setup references.
        return {
            "setupId": TRYON_DEFAULT_SETUP_ID,
            "name": "Fallback legacy setup",
            "isDefault": True,
            "revision": "legacy-fallback",
            "config": {"processing_profile": PROCESSING_PROFILE_MOTOGP},
            "payload": {
                "person_image_path": None,
                "garment_image_path": None,
                "output_image_path": None,
                "processing_profile": PROCESSING_PROFILE_MOTOGP,
            },
        }, "legacy"

    def ensure_default_setup_exists(self) -> None:
        now = now_iso()
        if self._load_setup_by_id(self.config.default_setup_id):
            return
        fallback = {
            "setupId": self.config.default_setup_id,
            "name": "MotoGP default",
            "description": "Default processing setup for local MotoGP leather try-on.",
            "active": True,
            "isDefault": True,
            "rank": 0,
            "revision": "legacy-fallback",
            "createdAt": now,
            "updatedAt": now,
        }
        self.setups.update_one(
            {"setupId": self.config.default_setup_id},
            {
                "$setOnInsert": {
                    "setupId": self.config.default_setup_id,
                    "name": fallback["name"],
                    "description": fallback["description"],
                    "active": True,
                    "isDefault": True,
                    "rank": 0,
                    "revision": "legacy-fallback",
                    "createdAt": now,
                    "updatedAt": now,
                },
            },
            upsert=True,
        )

    def emit_event(self, *, level: str, event: str, status: str, stage: str, job_id: str | None = None, details: dict[str, Any] | None = None) -> None:
        append_worker_event(
            {
                "jobId": job_id,
                "at": now_iso(),
                "level": level,
                "event": event,
                "status": status,
                "stage": stage,
                "details": details or {},
            }
        )

    def update_runtime_status(self, **patch: Any) -> None:
        payload = {
            "workerRunning": True,
            "workerId": self.config.worker_id,
            "currentJobId": self.current_job_id,
            "lastLoopAt": now_iso(),
            "pollIntervalSeconds": self.config.poll_interval_seconds,
            "enabled": self.config.worker_enabled,
        }
        payload.update(patch)
        write_worker_status(payload)

    def recover_stale_jobs(self) -> int:
        now = now_iso()
        result = self.jobs.update_many(
            {
                "status": {"$in": ["claimed", "processing", "uploading_result", "notifying_camera"]},
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
        if result.modified_count:
            self.emit_event(
                level="warn",
                event="recovered_stale_jobs",
                status="retry_wait",
                stage="failed",
                details={"count": int(result.modified_count)},
            )
        return int(result.modified_count)

    def recover_stale_heartbeat_jobs(self) -> int:
        now = datetime.now(UTC)
        stale_seconds = max(180, int(self.config.lease_duration_seconds * 2))
        heartbeat_threshold = (now - timedelta(seconds=stale_seconds)).isoformat().replace("+00:00", "Z")
        now_iso_string = now_iso()
        result = self.jobs.update_many(
            {
                "status": {"$in": ["processing", "uploading_result", "notifying_camera"]},
                "$or": [
                    {"processing.lastHeartbeatAt": {"$exists": False}},
                    {"processing.lastHeartbeatAt": None},
                    {"processing.lastHeartbeatAt": {"$lt": heartbeat_threshold}},
                ],
            },
            {
                "$set": {
                    "status": "retry_wait",
                    "stage": "failed",
                    "updatedAt": now_iso_string,
                    "processing.nextAttemptAt": plus_minutes(now_iso_string, 5),
                    "processing.leaseExpiresAt": None,
                    "processing.lastHeartbeatAt": None,
                    "error": {
                        "code": "processing_stalled",
                        "message": "processing heartbeat stale",
                        "details": "no heartbeat within configured window",
                    },
                }
            },
        )
        if result.modified_count:
            self.emit_event(
                level="warn",
                event="recovered_stale_heartbeat_jobs",
                status="retry_wait",
                stage="failed",
                details={"count": int(result.modified_count), "stale_seconds": stale_seconds},
            )
        return int(result.modified_count)

    def recover_operator_aborted_jobs(self) -> int:
        selector = {
            "status": "failed",
            "stage": "aborted",
            "error.code": "operator_aborted",
            "error.message": {"$regex": r"job_aborted_for_priority_push|priority_push", "$options": "i"},
        }
        now = now_iso()
        result = self.jobs.update_many(
            selector,
            {
                "$set": {
                    "status": "queued",
                    "stage": "queued",
                    "updatedAt": now,
                    "error": {"code": None, "message": None, "details": None},
                    "processing.leaseExpiresAt": None,
                    "processing.nextAttemptAt": None,
                    "processing.finishedAt": None,
                    "processing.attemptCount": 0,
                },
                "$unset": {
                    "processing.publicationError": "",
                    "processing.startedAt": "",
                },
            },
        )
        if result.modified_count:
            self.emit_event(
                level="warn",
                event="recovered_aborted_jobs",
                status="queued",
                stage="queued",
                details={"count": int(result.modified_count)},
            )
        return int(result.modified_count)

    def claim_next_job(self) -> dict[str, Any] | None:
        now = now_iso()
        job = self.jobs.find_one_and_update(
            {
                "status": {"$in": ["queued", "retry_wait"]},
                "$and": [
                    {
                        "$or": [
                            {"processing.nextAttemptAt": {"$exists": False}},
                            {"processing.nextAttemptAt": None},
                            {"processing.nextAttemptAt": {"$lte": now}},
                        ]
                    },
                    {
                        "$or": [
                            {"processing.leaseExpiresAt": None},
                            {"processing.leaseExpiresAt": {"$exists": False}},
                            {"processing.leaseExpiresAt": {"$lt": now}},
                        ]
                    },
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
        if job:
            self.emit_event(
                level="info",
                event="claimed_job",
                status="claimed",
                stage="claimed",
                job_id=str(job.get("jobId") or ""),
                details={"attemptCount": int((job.get("processing") or {}).get("attemptCount", 0))},
            )
        return job

    def update_stage(self, job_id: str, status: str, stage: str, patch: dict[str, Any] | None = None) -> None:
        payload = {"status": status, "stage": stage, "updatedAt": now_iso()}
        if patch:
            payload.update(patch)
        self.jobs.update_one({"jobId": job_id}, {"$set": payload})
        self.emit_event(level="info", event="stage_transition", status=status, stage=stage, job_id=job_id, details=patch or {})

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
        self.update_runtime_status(lastHeartbeatAt=now)

    def schedule_retry_or_failure(self, job: dict[str, Any], code: str, message: str, details: str | None = None) -> str:
        attempt_count = int(job.get("processing", {}).get("attemptCount", 0))
        if code != "transient_runtime_error":
            now = now_iso()
            self.jobs.update_one(
                {"jobId": job["jobId"]},
                {
                    "$set": {
                        "error": {"code": code, "message": message, "details": details},
                        "updatedAt": now,
                        "status": "failed",
                        "stage": "failed",
                        "processing.finishedAt": now,
                        "processing.leaseExpiresAt": None,
                    },
                    "$unset": {"processing.nextAttemptAt": ""},
                },
            )
            return "failed"
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

    def _refresh_job(self, job_id: str) -> dict[str, Any]:
        return self.jobs.find_one({"jobId": job_id}) or {}

    def _is_camera_notified(self, job: dict[str, Any]) -> bool:
        return bool((job.get("processing") or {}).get("cameraNotifiedAt"))

    def _has_published_url(self, result: dict[str, Any]) -> bool:
        return bool(str(result.get("publicResultUrl") or "").strip())

    def _build_camera_completion_payload_variants(self, source_submission_id: str, source: dict[str, Any]) -> list[dict[str, Any]]:
        variants: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(candidate: str | None) -> None:
            normalized = (candidate or "").strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            variants.append({"submissionId": normalized, "sourceSubmissionId": normalized})
            variants.append({"submissionId": normalized})
            variants.append({"sourceSubmissionId": normalized})
            variants.append({"submission": {"id": normalized}})

        add(source_submission_id)
        add(source.get("eventMongoId"))
        if _looks_like_hex_submission_id(source.get("eventId")):
            add(source.get("eventId"))
        add(source.get("sourceSubmissionId"))
        return variants or [{"submissionId": "", "sourceSubmissionId": "", "submission": {"id": ""}}]

    def _is_camera_completion_submission_invalid(self, body: str) -> bool:
        return "source submission is invalid" in (body or "").lower()

    def _is_camera_completion_retryable_status(self, status_code: int, body: str) -> bool:
        if self._is_camera_completion_submission_invalid(body):
            return False
        return status_code in {408, 409, 429, 500, 502, 503, 504}

    def _mark_publication_error(self, job_id: str, code: str, message: str, details: str | None = None) -> None:
        now = now_iso()
        self.jobs.update_one(
            {"jobId": job_id},
            {
                "$set": {
                    "processing.publicationError": {
                        "code": code,
                        "message": message,
                        "details": details,
                        "occurredAt": now,
                    },
                    "updatedAt": now,
                }
            },
        )

    def _clear_publication_error(self, job_id: str) -> None:
        self.jobs.update_one({"jobId": job_id}, {"$unset": {"processing.publicationError": ""}})

    def _upsert_publication_result(self, job_id: str, upload: dict[str, Any], now: str) -> None:
        self.jobs.update_one(
            {"jobId": job_id},
            {
                "$set": {
                    "status": "uploading_result",
                    "stage": "uploaded_result",
                    "updatedAt": now,
                    "result": {
                        "publicResultUrl": upload["imageUrl"],
                        "deleteUrl": upload.get("deleteUrl"),
                        "imgbbDeleteUrl": upload.get("deleteUrl"),
                        "provider": "imgbb",
                        "uploadedAt": now,
                    },
                    "processing.publicationState": PUBLICATION_STATE_UPLOADED,
                },
                "$unset": {"processing.publicationError": ""},
            },
        )

    def _mark_camera_notified(self, job_id: str, now: str) -> None:
        self.jobs.update_one(
            {"jobId": job_id},
            {
                "$set": {
                    "processing.cameraNotifiedAt": now,
                    "processing.publicationState": PUBLICATION_STATE_CAMERA_NOTIFIED,
                    "updatedAt": now,
                },
                "$unset": {"processing.publicationError": ""},
            },
        )

    def ensure_published_result(self, job_id: str, result_path: Path, *, job_snapshot: dict[str, Any]) -> dict[str, Any]:
        latest_job = self._refresh_job(job_id) or job_snapshot
        result_state = latest_job.get("result") or {}
        public_result_url = str(result_state.get("publicResultUrl") or "").strip()
        if self._has_published_url(result_state):
            self.emit_event(
                level="info",
                event="imgbb_reused",
                status="uploading_result",
                stage="uploaded_result",
                job_id=job_id,
                details={"publicResultUrl": redact_url(public_result_url), "provider": str(result_state.get("provider") or "imgbb")},
            )
            self._clear_publication_error(job_id)
            return {"imageUrl": public_result_url, "deleteUrl": result_state.get("deleteUrl") or result_state.get("imgbbDeleteUrl")}

        self.update_stage(job_id, "uploading_result", "uploading_result")
        self.jobs.update_one(
            {"jobId": job_id},
            {
                "$set": {
                    "processing.publicationState": PUBLICATION_STATE_NOT_STARTED,
                    "updatedAt": now_iso(),
                }
            },
        )
        upload = self.upload_to_imgbb(result_path)
        now = now_iso()
        self._upsert_publication_result(job_id, upload, now)
        self.emit_event(
            level="info",
            event="imgbb_uploaded",
            status="uploading_result",
            stage="uploaded_result",
            job_id=job_id,
            details={"publicResultUrl": redact_url(upload["imageUrl"])},
        )
        return upload

    def ensure_camera_notified(self, job_id: str, upload: dict[str, Any]) -> bool:
        latest_job = self._refresh_job(job_id)
        setup_id = str((latest_job.get("processing") or {}).get("resolvedSetupId") or TRYON_DEFAULT_SETUP_ID)
        setup_source = str((latest_job.get("processing") or {}).get("resolvedSetupSource") or "legacy")
        setup_profile = str((latest_job.get("processing") or {}).get("resolvedSetupProfile") or PROCESSING_PROFILE_MOTOGP)
        setup_revision = str((latest_job.get("processing") or {}).get("resolvedSetupRevision") or "")
        source = latest_job.get("source") or {}
        submission_id = str(source.get("submissionId") or "")
        latest_job = self._refresh_job(job_id)
        if self._is_camera_notified(latest_job):
            self._clear_publication_error(job_id)
            self.emit_event(
                level="info",
                event="camera_completion_skipped",
                status="notifying_camera",
                stage="notifying_camera",
                job_id=job_id,
                details={"reason": "already_notified"},
            )
            return False

        self.update_stage(job_id, "notifying_camera", "notifying_camera")
        self.notify_camera_completion(
            job_id,
            upload,
            source=source,
            submission_id=submission_id,
            setup_id=setup_id,
            setup_source=setup_source,
            processing_profile=setup_profile,
            setup_revision=setup_revision,
        )
        now = now_iso()
        self._mark_camera_notified(job_id, now)
        self.emit_event(
            level="info",
            event="camera_completion_succeeded",
            status="notifying_camera",
            stage="notifying_camera",
            job_id=job_id,
        )
        return True

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

    def download_source_image(
        self,
        image_url: str,
        destination: Path,
        *,
        allowed_hosts: list[str],
        max_bytes: int,
        asset_type: str,
    ) -> None:
        hostname = urlparse(image_url).hostname or ""
        if urlparse(image_url).scheme.lower() != "https":
            raise RuntimeError(f"{asset_type}_scheme_invalid")
        if hostname.lower() not in allowed_hosts:
            raise RuntimeError(f"{asset_type}_host_not_allowlisted:{hostname}")

        response = requests.get(image_url, timeout=30, allow_redirects=self.config.allow_redirects)
        final_host = urlparse(response.url).hostname or hostname
        if final_host.lower() not in allowed_hosts:
            raise RuntimeError(f"{asset_type}_redirect_blocked:{final_host}")
        response.raise_for_status()
        content_type = str(response.headers.get("content-type") or "").lower()
        if not any(token in content_type for token in ("image/", "application/octet-stream")):
            raise RuntimeError(f"{asset_type}_content_type_invalid:{content_type or 'unknown'}")
        if len(response.content) > max_bytes:
            raise RuntimeError(f"{asset_type}_oversized:{len(response.content)}")
        destination.write_bytes(response.content)

    def stage_suit_asset(self, leather_suit_id: str, destination: Path) -> str:
        suit = self.suits.find_one({"leatherSuitId": leather_suit_id, "active": True})
        if not suit:
            raise RuntimeError(f"missing suit:{leather_suit_id}")
        suit = normalize_suit_document(suit)
        suit_errors = validate_suit_document(suit)
        if suit_errors:
            raise RuntimeError(",".join(suit_errors))

        remote_url = (
            str(suit.get("sourceImageUrl") or "").strip()
            or str(suit.get("imageUrl") or "").strip()
            or str(suit.get("previewUrl") or "").strip()
        )
        if remote_url:
            self.download_source_image(
                remote_url,
                destination,
                allowed_hosts=self.config.allowed_suit_source_hosts,
                max_bytes=self.config.max_suit_image_bytes,
                asset_type="suit",
            )
            return remote_url

        local_asset = self.resolve_local_suit_asset(suit)
        shutil.copyfile(local_asset, destination)
        return str(local_asset)

    def call_local_tryon_api(self, person_input_path: Path, suit_input_path: Path, output_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        payload["person_image_path"] = str(person_input_path)
        payload["garment_image_path"] = str(suit_input_path)
        payload["output_image_path"] = str(output_path)
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

    def _coerce_segmind_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        description = _safe_str(payload.get("garment_des")) or "Sport jersey with logos and text graphics."
        normalized_description = description.strip().rstrip(". ")
        lower_description = normalized_description.lower()
        constraints = [
            "the person is male",
            "keep the person masculine",
            "do not add female anatomy or breasts",
            "preserve logos and text on the garment exactly",
            "do not alter logo colors, fonts, or wording",
            "keep print, lettering, and branding details visually consistent",
            "never edit, replace, or redact any readable text on the garment",
            "preserve logo and text placement, scale, and proportions",
            "if transparent regions exist, treat alpha as a hard garment boundary and avoid filling transparent pixels",
        ]
        for constraint in constraints:
            if constraint not in lower_description:
                normalized_description = f"{normalized_description}. {constraint}"
                lower_description = normalized_description.lower()
        return {
            "crop": _coerce_bool(payload.get("crop"), fallback=False),
            "category": _normalize_segmind_category(payload.get("category")),
            "force_dc": _coerce_bool(payload.get("force_dc"), fallback=False),
            "mask_only": _coerce_bool(payload.get("mask_only"), fallback=False),
            "seed": _coerce_int(payload.get("seed"), fallback=42),
            "steps": _coerce_int(payload.get("steps"), fallback=30),
            "garment_des": normalized_description,
        }

    def _coerce_fal_category(self, value: Any) -> str:
        key = (_safe_str(value) or "").strip().lower()
        if key in {"one-piece", "onepieces", "onepiece", "one_pieces", "one pieces", "dresses", "dresses_only", "dress"}:
            return FAL_CATEGORY_ONE_PIECES
        if key in {"lower", "lower_body", "lower body", "bottoms", "bottom"}:
            return "bottoms"
        if key in {"upper", "upper_body", "upper body", "tops", "top"}:
            return "tops"
        return "auto"

    def _coerce_fal_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "model_image": _safe_str(payload.get("model_image")) or "",
            "garment_image": _safe_str(payload.get("garment_image")) or "",
            "category": self._coerce_fal_category(payload.get("category")),
            "mode": FAL_MODE_QUALITY,
            "garment_photo_type": "auto",
            "moderation_level": "permissive",
            "seed": _coerce_int(payload.get("seed"), FAL_MAX_BRAND_PRESERVATION_SEED),
            "num_samples": max(1, min(4, _coerce_int(payload.get("num_samples"), 1))),
            "segmentation_free": _coerce_bool(payload.get("segmentation_free"), True),
            "output_format": FAL_OUTPUT_FORMAT,
        }

    def _is_fal_configured(self) -> bool:
        return bool(self.config.fal_key and self.config.fal_tryon_model)

    def _is_fal_session_enabled(self) -> bool:
        return self._is_fal_configured() and not self._fal_session_disabled

    def _is_fal_auth_error(self, message: str) -> bool:
        lower = (message or "").lower()
        return any(
            token in lower
            for token in (
                "401",
                "403",
                "unauthorized",
                "forbidden",
                "invalid api key",
                "api key",
                "authentication",
                "access denied",
                "signature expired",
                "permission denied",
            )
        )

    def _disable_fal_session(self, reason: str, *, job_id: str | None = None, stage: str = "running_tryon") -> None:
        self._fal_session_disabled = True
        self._fal_session_disable_reason = (reason or "").strip()[:220] or None
        self.emit_event(
            level="warn",
            event="provider_fallback",
            status="processing",
            stage=stage,
            job_id=job_id,
            details={
                "from": PROCESSING_PROFILE_FAL_TRYON,
                "to": self._fallback_profile_for_fal(),
                "reason": "fal_auth_failed",
                "message": redact_url(self._fal_session_disable_reason or reason),
            },
        )

    def _probe_fal_session(self) -> bool:
        if not self._is_fal_configured():
            return False
        submit_url = self._fal_queue_submit_url()
        try:
            response = requests.post(
                submit_url,
                headers=self._fal_headers(),
                json={},
                timeout=15,
            )
        except requests.RequestException as exc:
            self.emit_event(
                level="warn",
                event="provider_probe_failed",
                status="idle",
                stage="startup",
                details={
                    "provider": "fal",
                    "reason": "network_error",
                    "message": redact_url(str(exc)),
                },
            )
            print("[tryon-worker] startup: Fal probe failed due transport error; keeping Fal enabled for fallback-aware runtime.")
            return True

        if response.status_code in (401, 403):
            self._disable_fal_session(
                f"fal_health_check_http_{response.status_code}:{response.text[:200]}",
                stage="startup",
            )
            return False

        if response.status_code >= 400 and response.status_code not in (400, 422):
            if self._is_fal_auth_error(response.text):
                self._disable_fal_session(
                    f"fal_health_check_http_{response.status_code}:{response.text[:200]}",
                    stage="startup",
                )
                return False

            self.emit_event(
                level="warn",
                event="provider_probe_warning",
                status="idle",
                stage="startup",
                details={
                    "provider": "fal",
                    "reason": "invalid_request_payload",
                    "status": response.status_code,
                },
            )
            return True

        return True

    def _fallback_profile_for_fal(self) -> str:
        if self.config.segmind_api_key:
            return PROCESSING_PROFILE_SEGMIND_IDM_VTON
        return PROCESSING_PROFILE_MOTOGP

    def _is_fal_fallback_candidate(self, message: str) -> bool:
        lower = (message or "").lower()
        return any(
            token in lower
            for token in (
                "request exception",
                "request_error",
                "requesterror",
                "connection",
                "http error",
                "fal_api_key_missing",
                "fal_request_id_missing",
                "fal_status_failed",
                "fal_output_fetch_failed",
                "fal_output_missing",
                "fal_api_failed",
                "fal_api_no_output",
                "401",
                "403",
                "unauthorized",
                "forbidden",
                "timed out",
                "timeout",
            )
        )

    def _set_runtime_profile(self, job_id: str, requested_profile: str, runtime_profile: str, reason: str | None = None) -> None:
        update: dict[str, Any] = {"processing.resolvedSetupProfile": runtime_profile}
        if reason:
            update["processing.profileFallbackFrom"] = requested_profile
            update["processing.profileFallbackReason"] = reason
        else:
            update["processing.profileFallbackFrom"] = None
            update["processing.profileFallbackReason"] = None
        self.jobs.update_one({"jobId": job_id}, {"$set": update})

    def log_startup_status(self) -> None:
        if self._is_fal_session_enabled():
            details = {
                "fal": "configured",
                "model": self.config.fal_tryon_model,
            }
            self.emit_event(level="info", event="fal_ready", status="idle", stage="startup", details=details)
            print("[tryon-worker] startup: FAL try-on configured; direct Fal path available.")
            return

        if self._is_fal_configured():
            details = {
                "fal": "session_disabled",
                "fallbackProfile": self._fallback_profile_for_fal(),
                "reason": "fal_auth_failed",
            }
            self.emit_event(level="warn", event="provider_fallback", status="idle", stage="startup", details=details)
            print("[tryon-worker] startup: Fal auth/session marked unhealthy; using fallback provider.")
            return

        fallback_profile = self._fallback_profile_for_fal()
        details = {
            "fal": "not_configured",
            "fallbackProfile": fallback_profile,
        }
        if fallback_profile == PROCESSING_PROFILE_SEGMIND_IDM_VTON:
            details["reason"] = "auto-fallback-to-segmind"
            message = "[tryon-worker] startup: FAL not configured (missing key/model). Auto-fallback enabled to segmind_idm_vton first."
        else:
            details["reason"] = "auto-fallback-to-local"
            message = "[tryon-worker] startup: FAL not configured (missing key/model). Auto-fallback enabled to local motoGP path."

        self.emit_event(level="warn", event="provider_fallback", status="idle", stage="startup", details=details)
        print(message)

    def _fal_queue_submit_url(self) -> str:
        model = self.config.fal_tryon_model.strip().strip("/")
        base = (self.config.fal_base_url or "https://fal.run").strip().rstrip("/")
        if base.startswith("https://fal.run"):
            return f"https://queue.fal.run/{model}"
        if base.startswith("http://fal.run"):
            return f"http://queue.fal.run/{model}"
        return f"{base}/{model}"

    def _fal_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Key {self.config.fal_key}",
            "Content-Type": "application/json",
        }

    def _fal_model_path(self) -> str:
        return self.config.fal_tryon_model.strip().strip("/")

    def _coerce_fal_queue_url(self, base_url: str, request_id: str | None, *, suffix: str = "") -> str:
        if request_id:
            model = self._fal_model_path()
            base = base_url.rstrip("/")
            suffix_value = suffix.rstrip("/")
            return f"{base}/{model}/requests/{request_id}{suffix_value}"
        return (base_url.rstrip("/") + "/") if base_url else ""

    def _looks_like_fal_legacy_endpoint(self, value: str | None) -> bool:
        value = (value or "").strip()
        if not value:
            return False
        model = self._fal_model_path()
        if model and f"/{model}/" in value:
            return False
        parsed = urlparse(value)
        return parsed.netloc == "queue.fal.run" and "/requests/" in parsed.path

    def _is_queued_fal_submit_response(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        has_queue_url_fields = bool(set(payload.keys()) & {"request_id", "status_url", "response_url", "cancel_url", "queue_position"})
        status = _safe_str(payload.get("status")).upper()
        request_id = _safe_str(payload.get("request_id"))
        status_url = _safe_str(payload.get("status_url"))
        response_url = _safe_str(payload.get("response_url"))
        return (
            has_queue_url_fields
            or (status in {"IN_QUEUE", "IN_PROGRESS", "COMPLETED"} and bool(request_id))
            or (status_url.startswith("https://queue.fal.run/") if status_url else False)
            or (response_url.startswith("https://queue.fal.run/") if response_url else False)
        )

    def _coerce_fal_status_url(self, payload: dict[str, Any]) -> tuple[str | None, str | None]:
        request_id = _safe_str(payload.get("request_id"))
        status_url = _safe_str(payload.get("status_url"))
        response_url = _safe_str(payload.get("response_url"))

        if not response_url and request_id:
            response_url = self._coerce_fal_queue_url("https://queue.fal.run", request_id, suffix="/response")
        if not status_url and request_id:
            status_url = self._coerce_fal_queue_url("https://queue.fal.run", request_id, suffix="/status")
        if not response_url and status_url and status_url.endswith("/status"):
            response_url = status_url[:-len("/status")] + "/response"
        return status_url, response_url

    def _get_fal_output(self, result: dict[str, Any], output_path: Path) -> bool:
        if any(key in result for key in ("error", "error_type", "status") ) and not self._extract_first_http_url(result) and not self._extract_first_base64(result):
            error = result.get("error") or result.get("error_type")
            raise RuntimeError(f"fal_output_error:{error}")

        output_url = self._extract_first_http_url(result.get("images") or result.get("output") or result)
        if output_url:
            self._download_to_file(output_url, output_path)
            return True
        base64_payload = self._extract_first_base64(result.get("images") or result.get("output") or result)
        if base64_payload:
            output_path.write_bytes(base64.b64decode(base64_payload.encode("ascii"), validate=False))
            return True
        return False

    def _wait_for_fal_result(self, status_url: str, response_url: str | None) -> dict[str, Any]:
        start = time.time()
        headers = self._fal_headers()
        request_timeout = max(60, self.config.fal_tryon_timeout_seconds)
        while True:
            if (time.time() - start) > request_timeout:
                raise RuntimeError("fal_request_timeout")
            try:
                status_response = requests.get(
                    status_url,
                    headers=headers,
                    timeout=min(30, request_timeout),
                    params={"logs": 1},
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"fal_status_failed:request_error:{exc}")

            if status_response.status_code >= 400:
                raise RuntimeError(f"fal_status_failed:{status_response.status_code}:{status_response.text[:200]}")
            try:
                status_payload = status_response.json()
            except ValueError:
                raise RuntimeError(f"fal_status_failed:{status_response.status_code}:non_json")

            status = _safe_str(status_payload.get("status")).upper()
            if status == "COMPLETED":
                if response_url:
                    response_data = self._fetch_fal_response(response_url)
                    return response_data
                if isinstance(status_payload, dict) and "response" in status_payload and isinstance(status_payload["response"], dict):
                    return status_payload["response"]
                return status_payload

            if status in {"FAILED", "ERROR", "CANCELLED"}:
                error = status_payload.get("error") or status_payload.get("message") or "fal request failed"
                error_type = status_payload.get("error_type")
                raise RuntimeError(f"fal_status_failed:{error_type or 'failed'}:{error}")

            if status not in {"IN_QUEUE", "IN_PROGRESS"}:
                raise RuntimeError(f"fal_status_failed:unexpected_status:{status}")
            time.sleep(min(5, max(1, request_timeout // 6)))

    def _fetch_fal_response(self, response_url: str) -> dict[str, Any]:
        try:
            response = requests.get(
                response_url,
                headers=self._fal_headers(),
                timeout=min(60, max(15, self.config.fal_tryon_timeout_seconds)),
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"fal_output_fetch_failed:request_error:{exc}")

        if response.status_code >= 400:
            raise RuntimeError(f"fal_output_fetch_failed:{response.status_code}:{response.text[:200]}")
        try:
            response_payload = response.json()
        except ValueError:
            raise RuntimeError("fal_output_fetch_failed:non_json")
        return response_payload

    def call_fal_tryon_api(
        self,
        person_input_path: Path,
        garment_input_path: Path,
        output_path: Path,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.config.fal_key:
            raise RuntimeError("fal_api_key_missing")
        if not self.config.fal_base_url or not self.config.fal_tryon_model:
            raise RuntimeError("fal_api_config_missing")

        person_image_upload = self.upload_to_imgbb(person_input_path)
        garment_image_upload = self.upload_to_imgbb(garment_input_path)

        request_payload = self._coerce_fal_payload(
            {
                **payload,
                "model_image": person_image_upload["imageUrl"],
                "garment_image": garment_image_upload["imageUrl"],
            }
        )

        submit_url = self._fal_queue_submit_url()
        response = requests.post(
            submit_url,
            headers=self._fal_headers(),
            json=request_payload,
            timeout=min(30, max(10, self.config.fal_tryon_timeout_seconds)),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"fal_api_failed:{response.status_code}:{response.text[:300]}")

        try:
            submit_payload = response.json()
        except ValueError:
            if not response.content:
                raise RuntimeError("fal_request_id_missing")
            return self._write_fal_result_bytes(response, output_path)

        if not self._is_queued_fal_submit_response(submit_payload):
            direct_payload = {
                "images": submit_payload.get("images"),
                "output": submit_payload.get("output"),
                "result": submit_payload.get("result"),
            }
            if self._extract_first_http_url(direct_payload) or self._extract_first_base64(direct_payload):
                if self._get_fal_output(direct_payload, output_path):
                    return {"status": "succeeded", "source": "direct_result", "response": submit_payload}

        request_id = _safe_str(submit_payload.get("request_id"))
        if not request_id and not _safe_str(submit_payload.get("status_url")) and not _safe_str(submit_payload.get("response_url")):
            raise RuntimeError("fal_request_id_missing")

        status_url, response_url = self._coerce_fal_status_url(submit_payload)
        if not status_url:
            raise RuntimeError("fal_status_failed:no_status_url")

        result = self._wait_for_fal_result(status_url, response_url)
        if self._get_fal_output(result, output_path):
            return {"status": "succeeded", "source": "queued_result", "response": result}
        raise RuntimeError("fal_output_missing")

    def _write_fal_result_bytes(self, response: Any, output_path: Path) -> dict[str, Any]:
        if hasattr(response, "content"):
            content = response.content
        else:
            content = response
        if not content:
            raise RuntimeError("fal_output_missing")
        output_path.write_bytes(content)
        return {"status": "succeeded", "source": "raw_body"}

    def _image_mime_type(self, image_path: Path) -> str:
        extension = image_path.suffix.lower()
        if extension == ".png":
            return "image/png"
        if extension == ".webp":
            return "image/webp"
        return "image/jpeg"

    def _image_base64(self, image_path: Path) -> str:
        return base64.b64encode(image_path.read_bytes()).decode("utf-8")

    def _extract_first_http_url(self, value: Any) -> str | None:
        if isinstance(value, str):
            normalized = _safe_str(value)
            if normalized and _is_http_url(normalized):
                return normalized
            return None
        if isinstance(value, dict):
            for key in ("image", "imageUrl", "url", "image_url", "result", "output", "outputUrl"):
                candidate = self._extract_first_http_url(value.get(key))
                if candidate:
                    return candidate
            for candidate in value.values():
                found = self._extract_first_http_url(candidate)
                if found:
                    return found
            return None
        if isinstance(value, list):
            for candidate in value:
                found = self._extract_first_http_url(candidate)
                if found:
                    return found
        return None

    def _extract_first_base64(self, value: Any) -> str | None:
        if isinstance(value, str):
            normalized = _safe_str(value)
            if not normalized or _is_http_url(normalized):
                return None
            compact = normalized.strip()
            if compact.startswith("data:") and "," in compact:
                compact = compact.split(",", 1)[1]
                compact = compact.strip()
            try:
                base64.b64decode("".join(compact.split()), validate=True)
                return compact
            except Exception:
                return None
        if isinstance(value, dict):
            for candidate in value.values():
                found = self._extract_first_base64(candidate)
                if found:
                    return found
        if isinstance(value, list):
            for candidate in value:
                found = self._extract_first_base64(candidate)
                if found:
                    return found
        return None

    def _download_to_file(self, image_url: str, destination: Path) -> None:
        try:
            response = requests.get(image_url, timeout=120, allow_redirects=self.config.allow_redirects)
        except requests.RequestException as exc:
            raise RuntimeError(f"fal_output_fetch_failed:request_error:{exc}")

        if response.status_code >= 400:
            raise RuntimeError(f"fal_output_fetch_failed:{response.status_code}:{response.text[:200]}")
        if not response.content:
            raise RuntimeError("remote_output_download_empty")
        destination.write_bytes(response.content)

    def call_segmind_tryon_api(
        self,
        person_input_path: Path,
        garment_input_path: Path,
        output_path: Path,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.config.segmind_api_key:
            raise RuntimeError("segmind_api_key_missing")

        human_image_upload = self.upload_to_imgbb(person_input_path)
        garment_image_upload = self.upload_to_imgbb(garment_input_path)

        request_payload = self._coerce_segmind_payload(payload)
        request_payload["human_img"] = human_image_upload["imageUrl"]
        request_payload["garm_img"] = garment_image_upload["imageUrl"]
        if _has_alpha_channel(garment_input_path):
            request_payload["category"] = "dresses"
            request_payload["crop"] = False
            request_payload["force_dc"] = False
            request_payload["mask_only"] = False
            extra_prompt = (
                "For transparent garment PNG inputs, preserve the exact alpha edge boundary as a hard mask, "
                "never paint into transparent regions, and avoid halos or background bleeding."
            )
            lowered_prompt = request_payload["garment_des"].lower()
            if "alpha" not in lowered_prompt and "transparent garment png" not in lowered_prompt:
                request_payload["garment_des"] = f"{request_payload['garment_des']}. {extra_prompt}"

        response = requests.post(
            self.config.segmind_api_url,
            headers={"x-api-key": self.config.segmind_api_key, "Content-Type": "application/json"},
            json=request_payload,
            timeout=self.config.segmind_api_timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"segmind_api_failed:{response.status_code}:{response.text[:300]}")

        content_type = (response.headers.get("content-type") or "").lower()
        if content_type.startswith("image/"):
            if not response.content:
                raise RuntimeError("segmind_api_empty_output")
            output_path.write_bytes(response.content)
            return {"status": "succeeded", "source": "http_image_body"}

        try:
            result = response.json()
        except ValueError:
            if not response.content:
                raise RuntimeError("segmind_api_missing_output_json")
            output_path.write_bytes(response.content)
            return {"status": "succeeded", "source": "raw_body"}

        image_url = self._extract_first_http_url(result)
        if image_url:
            self._download_to_file(image_url, output_path)
            return {"status": "succeeded", "source": "result_url", "response": result}

        base64_payload = self._extract_first_base64(result)
        if base64_payload:
            output_path.write_bytes(base64.b64decode(base64_payload.encode("ascii"), validate=False))
            return {"status": "succeeded", "source": "result_base64", "response": result}

        raise RuntimeError("segmind_api_no_output")

    def local_tryon_api_is_ready(self) -> bool:
        capabilities_url = self.config.local_tryon_api_url
        if capabilities_url.endswith("/api/tryon/run"):
            capabilities_url = capabilities_url[: -len("/api/tryon/run")] + "/api/capabilities"
        else:
            capabilities_url = capabilities_url.rstrip("/") + "/api/capabilities"
        try:
            response = requests.get(capabilities_url, timeout=10)
            if response.status_code >= 400:
                return False
            payload = response.json()
        except Exception:
            return False
        feature = (payload.get("features") or {}).get("try_on") or {}
        runtime = payload.get("runtime") or {}
        return feature.get("status") == "ready" and runtime.get("models_ready") is True

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

    def notify_camera_completion(
        self,
        job_id: str,
        upload: dict[str, Any],
        *,
        source: dict[str, Any] | None = None,
        submission_id: str | None = None,
        setup_id: str,
        setup_source: str,
        processing_profile: str,
        setup_revision: str | None = None,
    ) -> None:
        source_payload = source or {}
        normalized_submission_id = (submission_id or "").strip()
        payload_variants = self._build_camera_completion_payload_variants(
            normalized_submission_id,
            source_payload,
        )

        last_status = 0
        last_body = ""
        for payload_variant in payload_variants:
            payload = {
                "jobId": job_id,
                "publicResultUrl": upload["imageUrl"],
                "resultImageUrl": upload["imageUrl"],
                "deleteUrl": upload.get("deleteUrl"),
                "resultDeleteUrl": upload.get("deleteUrl"),
                "workerId": self.config.worker_id,
                "processorMeta": {
                    "pipelineVersion": PIPELINE_VERSION,
                    "processingProfile": processing_profile,
                    "resolvedSetupId": setup_id,
                    "setupSource": setup_source,
                    "resolvedSetupRevision": setup_revision,
                },
            }
            payload.update(payload_variant)
            response = requests.post(
                self.config.camera_complete_url,
                json=payload,
                headers={"x-camera-tryon-secret": self.config.camera_internal_secret},
                timeout=60,
            )
            last_status = response.status_code
            last_body = response.text or ""
            if response.status_code < 400:
                return
            if self._is_camera_completion_submission_invalid(last_body):
                continue
            if not self._is_camera_completion_retryable_status(last_status, last_body):
                break

        raise RuntimeError(f"camera_completion_failed:{last_status}:{last_body[:300]}")

    def process_job(self, job: dict[str, Any]) -> None:
        job = normalize_job_document(job)
        job_id = job["jobId"]
        self.current_job_id = job_id
        self.update_runtime_status(currentJobId=job_id, lastClaimedJobId=job_id)
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
        self.heartbeat(job_id)

        try:
            job_errors = validate_job_document(job)
            if job_errors:
                raise RuntimeError(",".join(job_errors))

            setup_payload, setup_source = self.resolve_setup(job)
            payload = setup_payload["payload"]
            resolved_setup_id = setup_payload["setupId"]
            resolved_setup_name = setup_payload.get("name")
            resolved_setup_revision = setup_payload.get("revision")
            requested_processing_profile = str(payload.get("processing_profile") or PROCESSING_PROFILE_MOTOGP)
            processing_profile = requested_processing_profile
            request_profile = normalize_processing_profile(str((job.get("request") or {}).get("processingProfile") or ""))
            if request_profile == PROCESSING_PROFILE_FAL_TRYON:
                processing_profile = PROCESSING_PROFILE_FAL_TRYON
                requested_processing_profile = processing_profile
                payload["processing_profile"] = processing_profile
            if processing_profile == PROCESSING_PROFILE_FAL_TRYON and not self._is_fal_session_enabled():
                fallback_profile = self._fallback_profile_for_fal()
                if fallback_profile != PROCESSING_PROFILE_FAL_TRYON:
                    processing_profile = fallback_profile
                    payload["processing_profile"] = processing_profile
                    self._set_runtime_profile(
                        job_id,
                        requested_processing_profile,
                        processing_profile,
                        reason="fal_not_configured",
                    )
                    self.emit_event(
                        level="warn",
                        event="profile_fallback",
                        status="processing",
                        stage="running_tryon",
                        job_id=job_id,
                        details={
                            "from": requested_processing_profile,
                            "to": processing_profile,
                            "reason": "FAL is unavailable; auto-routed to fallback provider.",
                        },
                    )
                else:
                    raise RuntimeError("fal_not_configured")
            else:
                self._set_runtime_profile(job_id, processing_profile, processing_profile)
            if request_camera_id := self._normalize_setup_id(str((job.get("source") or {}).get("cameraId") or (job.get("request") or {}).get("cameraId") or "")):
                self._mark_camera_last_setup(request_camera_id, resolved_setup_id)
            self.jobs.update_one(
                {"jobId": job_id},
                {
                    "$set": {
                        "processing.resolvedSetupId": resolved_setup_id,
                        "processing.resolvedSetupName": resolved_setup_name,
                        "processing.resolvedSetupSource": setup_source,
                        "processing.resolvedSetupRevision": resolved_setup_revision,
                        "processing.resolvedSetupProfile": processing_profile,
                    }
                },
            )

            existing_job = self._refresh_job(job_id)
            if existing_job and self._is_camera_notified(existing_job):
                done_payload = {
                    "status": "done",
                    "stage": "done",
                    "updatedAt": now_iso(),
                    "processing.finishedAt": now_iso(),
                    "processing.leaseExpiresAt": None,
                    "processing.lastHeartbeatAt": now_iso(),
                    "error": {"code": None, "message": None, "details": None},
                }
                self._clear_publication_error(job_id)
                self.jobs.update_one({"jobId": job_id}, {"$set": done_payload})
                self.update_runtime_status(currentJobId=None, lastSuccessAt=now_iso())
                target = self.config.queue_root / "done" / job_id
                target.parent.mkdir(parents=True, exist_ok=True)
                if workspace_root.exists():
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.move(str(workspace_root), str(target))
                return

            existing_result = existing_job.get("result") or {}
            if self._has_published_url(existing_result):
                self.update_stage(job_id, "uploading_result", "uploaded_result")
                upload = self.ensure_published_result(job_id, result_path, job_snapshot=existing_job)
                self.ensure_camera_notified(job_id, upload)
                done_payload = {
                    "status": "done",
                    "stage": "done",
                    "updatedAt": now_iso(),
                    "processing.finishedAt": now_iso(),
                    "processing.leaseExpiresAt": None,
                    "processing.lastHeartbeatAt": now_iso(),
                    "error": {"code": None, "message": None, "details": None},
                }
                self.jobs.update_one({"jobId": job_id}, {"$set": done_payload})
                self.update_runtime_status(currentJobId=None, lastSuccessAt=now_iso())
                target = self.config.queue_root / "done" / job_id
                target.parent.mkdir(parents=True, exist_ok=True)
                if workspace_root.exists():
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.move(str(workspace_root), str(target))
                return

            self.update_stage(job_id, "processing", "downloading_input", {"processing.startedAt": now_iso()})
            self.download_source_image(
                job["source"]["imageUrl"],
                person_input_path,
                allowed_hosts=self.config.allowed_person_source_hosts,
                max_bytes=self.config.max_source_image_bytes,
                asset_type="source",
            )

            self.update_stage(job_id, "processing", "resolving_suit")
            resolved_suit_source = self.stage_suit_asset(job["request"]["leatherSuitId"], suit_input_path)

            metadata_path.write_text(
                json.dumps(
                    {
                        "jobId": job_id,
                        "submissionId": job["source"]["submissionId"],
                        "leatherSuitId": job["request"]["leatherSuitId"],
                        "workerId": self.config.worker_id,
                        "sourceImageUrl": redact_url(job["source"]["imageUrl"]),
                        "resolvedSuitAssetPath": redact_url(resolved_suit_source),
                        "processingProfile": processing_profile,
                        "setupId": resolved_setup_id,
                        "setupName": resolved_setup_name,
                        "setupSource": setup_source,
                        "createdAt": now_iso(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            self.update_stage(job_id, "processing", "running_tryon")
            if processing_profile == PROCESSING_PROFILE_SEGMIND_IDM_VTON:
                _normalize_segmind_aspect(person_input_path)
                _normalize_segmind_aspect(suit_input_path)
                api_result = self.call_segmind_tryon_api(person_input_path, suit_input_path, result_path, payload)
            elif processing_profile == PROCESSING_PROFILE_FAL_TRYON:
                try:
                    api_result = self.call_fal_tryon_api(person_input_path, suit_input_path, result_path, payload)
                except Exception as exc:  # noqa: BLE001
                    message = str(exc)
                    if self._is_fal_fallback_candidate(message):
                        if self._is_fal_auth_error(message):
                            self._disable_fal_session(message, job_id=job_id)
                        fallback_profile = self._fallback_profile_for_fal()
                        if fallback_profile != PROCESSING_PROFILE_FAL_TRYON:
                            self._set_runtime_profile(
                                job_id,
                                requested_processing_profile,
                                fallback_profile,
                                reason=f"fal_fallback:{message}",
                            )
                            payload["processing_profile"] = fallback_profile
                            processing_profile = fallback_profile
                            self.emit_event(
                                level="warn",
                                event="fal_fallback",
                                status="processing",
                                stage="running_tryon",
                                job_id=job_id,
                                details={
                                    "from": requested_processing_profile,
                                    "to": fallback_profile,
                                    "message": redact_url(message),
                                },
                            )
                            if fallback_profile == PROCESSING_PROFILE_SEGMIND_IDM_VTON:
                                _normalize_segmind_aspect(person_input_path)
                                _normalize_segmind_aspect(suit_input_path)
                                api_result = self.call_segmind_tryon_api(
                                    person_input_path,
                                    suit_input_path,
                                    result_path,
                                    payload,
                                )
                            else:
                                if not self.local_tryon_api_is_ready():
                                    raise RuntimeError("local_tryon_api_not_ready")
                                api_result = self.call_local_tryon_api(person_input_path, suit_input_path, result_path, payload)
                        else:
                            raise
                    else:
                        raise
                else:
                    payload["processing_profile"] = PROCESSING_PROFILE_FAL_TRYON
            else:
                if not self.local_tryon_api_is_ready():
                    raise RuntimeError("local_tryon_api_not_ready")
                api_result = self.call_local_tryon_api(person_input_path, suit_input_path, result_path, payload)
            append_log(log_path, json.dumps({"stage": "running_tryon", "response": api_result}))
            upload = self.ensure_published_result(job_id, result_path, job_snapshot=job)
            self.ensure_camera_notified(job_id, upload)

            done_payload = {
                "status": "done",
                "stage": "done",
                "updatedAt": now_iso(),
                "processing.finishedAt": now_iso(),
                "processing.leaseExpiresAt": None,
                "processing.lastHeartbeatAt": now_iso(),
                "error": {"code": None, "message": None, "details": None},
            }
            self.jobs.update_one({"jobId": job_id}, {"$set": done_payload})
            self.update_runtime_status(currentJobId=None, lastSuccessAt=now_iso())
            target = self.config.queue_root / "done" / job_id
            target.parent.mkdir(parents=True, exist_ok=True)
            if workspace_root.exists():
                if target.exists():
                    shutil.rmtree(target)
                shutil.move(str(workspace_root), str(target))
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            transient, code = classify_failure(message)
            latest_job = job
            outcome = "failed"
            try:
                latest_job = self.jobs.find_one({"jobId": job_id}) or job
                if "imgbb_upload" in message:
                    self._mark_publication_error(job_id, "imgbb_upload", message)
                elif "camera_completion_failed" in message:
                    self._mark_publication_error(job_id, "camera_completion", message)
            except Exception as lookup_error:  # noqa: BLE001
                append_log(log_path, json.dumps({"stage": "failure_lookup_error", "error": redact_url(str(lookup_error))}))
            try:
                outcome = self.schedule_retry_or_failure(latest_job, code, message)
            except Exception as schedule_error:  # noqa: BLE001
                append_log(log_path, json.dumps({"stage": "failure_schedule_error", "error": redact_url(str(schedule_error))}))
            append_log(log_path, json.dumps({"stage": outcome, "error": redact_url(message)}))
            self.emit_event(
                level="error",
                event="job_failed",
                status=outcome,
                stage="failed",
                job_id=job_id,
                details={"code": code, "message": redact_url(message)},
            )
            self.update_runtime_status(
                currentJobId=None,
                lastFailureAt=now_iso(),
                lastFailureCode=code,
                lastFailureMessage=redact_url(message),
            )
            target = self.config.queue_root / "failed" / job_id
            target.parent.mkdir(parents=True, exist_ok=True)
            if workspace_root.exists():
                if target.exists():
                    shutil.rmtree(target)
                shutil.move(str(workspace_root), str(target))
            if not transient and outcome == "failed":
                print(f"[tryon-worker] permanent failure {job_id}: {redact_url(message)}", file=sys.stderr)
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=2)
            self.current_job_id = None
            self.update_runtime_status(currentJobId=None, lastLoopAt=now_iso())

    def run_once(self) -> bool:
        if not self.config.worker_enabled:
            self.update_runtime_status(currentJobId=None, workerRunning=True, note="worker_disabled")
            print("[tryon-worker] worker disabled by settings")
            return False
        self.recover_stale_jobs()
        self.recover_stale_heartbeat_jobs()
        self.recover_operator_aborted_jobs()
        job = self.claim_next_job()
        if not job:
            self.update_runtime_status(currentJobId=None)
            print("[tryon-worker] no claimable jobs found")
            return False
        print(f"[tryon-worker] claimed job {job['jobId']}")
        self.process_job(job)
        return True

    def run_forever(self) -> None:
        ensure_queue_dirs(self.config.queue_root)
        self.emit_event(level="info", event="worker_started", status="idle", stage="startup", details={"pollIntervalSeconds": self.config.poll_interval_seconds})
        self.update_runtime_status(currentJobId=None, lastLoopAt=now_iso())
        while True:
            self.config = load_config()
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001
                message = redact_url(str(exc))
                self.emit_event(level="error", event="worker_loop_failed", status="failed", stage="worker_loop", details={"message": message})
                self.update_runtime_status(currentJobId=None, lastFailureAt=now_iso(), lastFailureCode="worker_loop_failed", lastFailureMessage=message)
                print(f"[tryon-worker] worker loop failure: {message}", file=sys.stderr)
            time.sleep(self.config.poll_interval_seconds)


def main() -> int:
    try:
        config = load_config()
    except RuntimeError as exc:
        print(f"[tryon-worker] hard-failure prerequisite missing: {exc}")
        print("[tryon-worker] required environment variables: MONGODB_ATLAS_URI/MONGODB_URI, MONGODB_DB_NAME/MONGODB_DB, CAMERA_TRYON_COMPLETE_URL, CAMERA_TRYON_INTERNAL_SECRET, IMGBB_API_KEY")
        return 1

    ensure_queue_dirs(config.queue_root)
    process_lock = SingleTaskLock("queue-worker-process", app_root=REPO_ROOT)
    if not process_lock.acquire(blocking=False):
        print("[tryon-worker] another queue worker is already running; exiting")
        return 0
    worker = TryOnQueueWorker(config)
    if not worker._probe_fal_session():
        print("[tryon-worker] startup: Fal health probe indicates auth/config issue; worker will start with fallback provider.")
    worker.log_startup_status()
    try:
        worker.update_runtime_status(currentJobId=None, lastLoopAt=now_iso())
        run_once = "--once" in sys.argv
        if run_once:
            worker.run_once()
            return 0
        worker.run_forever()
        return 0
    finally:
        process_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
