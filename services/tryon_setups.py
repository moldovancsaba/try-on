from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os

from services.worker_contracts import (
    PROCESSING_PROFILE_FAL_TRYON,
    PROCESSING_PROFILE_SEGMIND_IDM_VTON,
)


TRYON_SETUP_FIELD_ALLOWLIST = {
    "processing_profile",
    "crop",
    "category",
    "sleeve_length",
    "pant_length",
    "resolution",
    "force_dc",
    "steps",
    "guidance",
    "mask_only",
    "seed",
    "show_mask",
    "mask_sharpness",
    "mask_padding",
    "detail_boost",
    "face_restore_strength",
    "preserve_head",
    "lock_seed",
    "use_vae_hf",
    "sampler_name",
    "composite_strength",
    "enable_deep_texture",
    "warp_strength",
    "garment_des",
}

SETUP_CATALOG_ENV = "TRYON_SETUP_CATALOG_PATH"
DEFAULT_SETUP_CATALOG_PATH = ".config/tryon_setups.json"
SETUP_PROVIDER_LOCAL = "local"
SETUP_PROVIDER_ONLINE = "online"


def _normalize_provider(value: Any, *, processing_profile: str = "") -> str:
    raw = _normalize_text(value)
    if raw:
        raw = raw.lower()
        if raw in {"local", "online", "cloud"}:
            return "online" if raw == "cloud" else raw

    profile = processing_profile.strip().lower()
    if profile in {
        PROCESSING_PROFILE_SEGMIND_IDM_VTON,
        PROCESSING_PROFILE_FAL_TRYON,
    }:
        return SETUP_PROVIDER_ONLINE
    return SETUP_PROVIDER_LOCAL



def _normalize_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def _coerce_setup_entry(entry: dict[str, Any], *, source_path: Path) -> dict[str, Any] | None:
    setup_id = _normalize_text(entry.get("setupId"))
    if not setup_id:
        return None

    config = entry.get("config") or {}
    if not isinstance(config, dict):
        config = {}

    camera_id = _normalize_text(entry.get("cameraId"))
    description = _normalize_text(entry.get("description"))
    provider = _normalize_provider(entry.get("provider"), processing_profile=str(config.get("processing_profile", "")))

    return {
        "setupId": setup_id,
        "name": _normalize_text(entry.get("name")) or setup_id,
        "description": description,
        "cameraId": camera_id,
        "provider": provider,
        "active": bool(entry.get("active", True)),
        "isDefault": bool(entry.get("isDefault", False)),
        "rank": _normalize_int(entry.get("rank"), 0),
        "revision": _normalize_text(entry.get("revision")),
        "config": {key: config[key] for key in config if key in TRYON_SETUP_FIELD_ALLOWLIST},
        "_catalogPath": str(source_path),
    }


def load_local_setups(app_root: Path, catalog_path: str | None = None) -> dict[str, dict[str, Any]]:
    path_value = _normalize_text(catalog_path) or _normalize_text(os.getenv(SETUP_CATALOG_ENV)) or DEFAULT_SETUP_CATALOG_PATH
    catalog_path_obj = Path(path_value)
    if not catalog_path_obj.is_absolute():
        catalog_path_obj = app_root / catalog_path_obj

    if not catalog_path_obj.exists():
        return {}

    try:
        payload = json.loads(catalog_path_obj.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if isinstance(payload, dict):
        payload = payload.get("setups", [])

    if not isinstance(payload, list):
        return {}

    setups: dict[str, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        setup = _coerce_setup_entry(item, source_path=catalog_path_obj)
        if setup:
            setups[setup["setupId"]] = setup
    return setups
