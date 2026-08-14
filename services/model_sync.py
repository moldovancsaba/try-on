"""Deterministic provisioning of the shared model vault.

Turns the ASSETS declarations in services.capabilities into downloads, so the vault is
reproducible from the contract rather than from whatever a machine happens to have.
`install.sh` runs the "core" profile; `scripts/sync_models.py` exposes it to operators.

Downloads are resumable-ish rather than transactional: url_files skips a file that
already exists (so a truncated download is never repaired — delete it to refetch), and
hf snapshots rely on huggingface_hub's own caching. Always re-check readiness through
build_capability_report afterwards instead of trusting that a sync "finished".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.request import urlretrieve

from huggingface_hub import snapshot_download

from services.capabilities import ASSET_MAP, build_capability_report


SYNC_PROFILES: dict[str, tuple[str, ...]] = {
    "core": (
        "catvton_densepose",
        "catvton_schp",
        "sd15_inpainting",
        "sd15_vae",
        "gfpgan_face_restore",
        "google_edge_mediapipe",
    ),
    "optional": (),
}
SYNC_PROFILES["all"] = SYNC_PROFILES["core"] + SYNC_PROFILES["optional"]


def resolve_profile(profile: str) -> tuple[str, ...]:
    if profile not in SYNC_PROFILES:
        raise ValueError(f"Unsupported sync profile: {profile}")
    return SYNC_PROFILES[profile]


def plan_sync(models_root: Path, profile: str) -> dict[str, Any]:
    asset_keys = resolve_profile(profile)
    report = build_capability_report(models_root)
    assets = []
    for asset_key in asset_keys:
        asset_info = report["assets"][asset_key]
        assets.append(
            {
                "key": asset_key,
                "label": asset_info["label"],
                "path": asset_info["path"],
                "ready": asset_info["ready"],
                "source": asset_info["source"],
            }
        )
    return {"profile": profile, "models_root": str(models_root), "assets": assets}


def _download_hf_snapshot(models_root: Path, source: dict[str, Any]) -> None:
    target_relative_path = source.get("target_relative_path")
    local_dir = models_root / target_relative_path if target_relative_path else None
    if local_dir is None:
        raise ValueError("hf_snapshot source requires target_relative_path or asset-relative path resolution.")
    local_dir.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {
        "repo_id": source["repo_id"],
        "local_dir": str(local_dir),
        "max_workers": 4,
    }
    allow_patterns = source.get("allow_patterns")
    if allow_patterns:
        kwargs["allow_patterns"] = list(allow_patterns)
    snapshot_download(**kwargs)


def _download_url_files(asset_path: Path, source: dict[str, Any]) -> None:
    asset_path.mkdir(parents=True, exist_ok=True)
    for filename, url in source["files"].items():
        destination = asset_path / filename
        if destination.exists():
            continue
        urlretrieve(url, destination)


def sync_asset(models_root: Path, asset_key: str) -> dict[str, Any]:
    """Fetch one vault asset and return its freshly evaluated readiness entry.

    Dispatches on the asset's declared source kind — an HF snapshot (optionally
    filtered by allow_patterns, so a large repo yields only the needed subtree) or a
    set of direct file urls. Raises ValueError for an unknown kind, KeyError for an
    unknown asset_key, and lets network errors propagate: a failed sync must be loud.

    The returned entry can still say ready=False if the source did not deliver every
    required file, which is the honest outcome and worth checking.
    """
    asset = ASSET_MAP[asset_key]
    asset_path = models_root / asset.relative_path
    source = asset.source or {}
    kind = source.get("kind")
    if kind == "hf_snapshot":
        target_relative_path = source.get("target_relative_path", asset.relative_path)
        sync_source = dict(source)
        sync_source["target_relative_path"] = target_relative_path
        _download_hf_snapshot(models_root, sync_source)
    elif kind == "url_files":
        _download_url_files(asset_path, source)
    else:
        raise ValueError(f"Unsupported source kind for {asset_key}: {kind}")
    report = build_capability_report(models_root)
    return report["assets"][asset_key]


def sync_profile(models_root: Path, profile: str) -> dict[str, Any]:
    results = []
    for asset_key in resolve_profile(profile):
        results.append(sync_asset(models_root, asset_key))
    report = build_capability_report(models_root)
    return {"results": results, "report": report}


def write_manifest(models_root: Path, report: dict[str, Any]) -> Path:
    target = models_root / "manifest.json"
    with target.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return target
