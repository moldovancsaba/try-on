from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULT_MODELS_ROOT = Path("/Users/Shared/Models")


def get_models_root() -> Path:
    return Path(os.environ.get("TRYON_MODELS_ROOT", str(DEFAULT_MODELS_ROOT))).expanduser().resolve()


def get_hf_home(models_root: Path | None = None) -> Path:
    root = models_root or get_models_root()
    return root / ".cache" / "huggingface"


def get_app_root() -> Path:
    return Path(__file__).resolve().parent


def get_app_config_dir(app_root: Path | None = None) -> Path:
    root = app_root or get_app_root()
    return root / ".config"


def get_settings_path(app_root: Path | None = None) -> Path:
    return get_app_config_dir(app_root) / "settings.json"


def get_legacy_settings_path(models_root: Path | None = None) -> Path:
    root = models_root or get_models_root()
    return root / "settings.json"


def ensure_app_config_dir(app_root: Path | None = None) -> Path:
    config_dir = get_app_config_dir(app_root)
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def load_settings(*, app_root: Path | None = None, models_root: Path | None = None) -> dict:
    settings_path = get_settings_path(app_root)
    legacy_path = get_legacy_settings_path(models_root)

    for candidate in (settings_path, legacy_path):
        if not candidate.exists():
            continue
        try:
            with candidate.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            continue
    return {}


def save_settings(settings: dict, *, app_root: Path | None = None) -> Path:
    settings_path = get_settings_path(app_root)
    ensure_app_config_dir(app_root)
    with settings_path.open("w", encoding="utf-8") as handle:
        json.dump(settings, handle)
    return settings_path


def migrate_legacy_settings(*, app_root: Path | None = None, models_root: Path | None = None) -> Path | None:
    settings_path = get_settings_path(app_root)
    legacy_path = get_legacy_settings_path(models_root)
    if settings_path.exists() or not legacy_path.exists():
        return None

    ensure_app_config_dir(app_root)
    try:
        with legacy_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        with settings_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return settings_path
    except Exception:
        return None
