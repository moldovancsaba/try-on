from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


STATUS_READY = "ready"
STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AssetSpec:
    key: str
    label: str
    relative_path: str
    required_files: tuple[str, ...] = ()
    optional: bool = False
    source: dict[str, Any] | None = None
    notes: str | None = None


@dataclass(frozen=True)
class FeatureSpec:
    key: str
    label: str
    required_assets: tuple[str, ...]
    optional_assets: tuple[str, ...] = ()
    core_feature: bool = False
ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec(
        key="catvton_densepose",
        label="CatVTON DensePose",
        relative_path="processors/catvton-segmentation/DensePose",
        required_files=("model_final_162be9.pkl", "densepose_rcnn_R_50_FPN_s1x.yaml"),
        source={
            "kind": "hf_snapshot",
            "repo_id": "zhengchong/CatVTON",
            "allow_patterns": ("DensePose/*",),
            "target_relative_path": "processors/catvton-segmentation",
        },
    ),
    AssetSpec(
        key="catvton_schp",
        label="CatVTON SCHP",
        relative_path="processors/catvton-segmentation/SCHP",
        required_files=("exp-schp-201908301523-atr.pth", "exp-schp-201908261155-lip.pth"),
        source={
            "kind": "hf_snapshot",
            "repo_id": "zhengchong/CatVTON",
            "allow_patterns": ("SCHP/*",),
            "target_relative_path": "processors/catvton-segmentation",
        },
    ),
    AssetSpec(
        key="sd15_inpainting",
        label="Stable Diffusion 1.5 Inpainting",
        relative_path="checkpoints/sd15-inpainting",
        required_files=("model_index.json", "unet/config.json", "vae/config.json"),
        source={
            "kind": "hf_snapshot",
            "repo_id": "runwayml/stable-diffusion-inpainting",
        },
    ),
    AssetSpec(
        key="sd15_vae",
        label="SD15 VAE",
        relative_path="vae/sd15-vae-ft-mse",
        required_files=("config.json",),
        source={
            "kind": "hf_snapshot",
            "repo_id": "stabilityai/sd-vae-ft-mse",
        },
    ),
    AssetSpec(
        key="gfpgan_face_restore",
        label="GFPGAN Face Restoration",
        relative_path="processors/face-restoration",
        required_files=("GFPGANv1.4.pth", "detection_Resnet50_Final.pth", "parsing_parsenet.pth"),
        optional=True,
        source={
            "kind": "url_files",
            "files": {
                "GFPGANv1.4.pth": "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth",
                "detection_Resnet50_Final.pth": "https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth",
                "parsing_parsenet.pth": "https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth",
            },
        },
    ),
)

ASSET_MAP = {asset.key: asset for asset in ASSETS}

FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        key="try_on",
        label="Try-On",
        required_assets=("catvton_densepose", "catvton_schp", "sd15_inpainting", "sd15_vae"),
        optional_assets=("gfpgan_face_restore",),
        core_feature=True,
    ),
)

FEATURE_MAP = {feature.key: feature for feature in FEATURES}


def _asset_root(models_root: Path, asset: AssetSpec) -> Path:
    return models_root / asset.relative_path


def _resolve_required_file(base: Path, relative_file: str) -> Path:
    if base.is_file():
        return base
    return base / relative_file


def evaluate_asset(models_root: Path, asset: AssetSpec) -> dict[str, Any]:
    asset_path = _asset_root(models_root, asset)
    exists = asset_path.exists()
    missing_files: list[str] = []
    if exists:
        for required_file in asset.required_files:
            if not _resolve_required_file(asset_path, required_file).exists():
                missing_files.append(required_file)
    else:
        missing_files.extend(asset.required_files or (asset_path.name,))
    return {
        "key": asset.key,
        "label": asset.label,
        "path": str(asset_path),
        "exists": exists,
        "optional": asset.optional,
        "ready": exists and not missing_files,
        "missing_files": missing_files,
        "notes": asset.notes,
        "source": asset.source,
    }


def _feature_status(required_ready: bool) -> str:
    if required_ready:
        return STATUS_READY
    return STATUS_UNAVAILABLE


def build_capability_report(models_root: Path, *, runtime_state: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime_state = runtime_state or {}
    assets = {asset.key: evaluate_asset(models_root, asset) for asset in ASSETS}
    features: dict[str, Any] = {}

    for feature in FEATURES:
        missing_required = [key for key in feature.required_assets if not assets[key]["ready"]]
        missing_optional = [key for key in feature.optional_assets if not assets[key]["ready"]]
        status = _feature_status(not missing_required)
        notes: list[str] = []
        if missing_required:
            notes.append(
                "Missing required assets: "
                + ", ".join(ASSET_MAP[key].label for key in missing_required)
            )
        if missing_optional:
            notes.append(
                "Missing optional assets: "
                + ", ".join(ASSET_MAP[key].label for key in missing_optional)
            )

        features[feature.key] = {
            "key": feature.key,
            "label": feature.label,
            "status": status,
            "core_feature": feature.core_feature,
            "required_assets": list(feature.required_assets),
            "optional_assets": list(feature.optional_assets),
            "missing_required_assets": missing_required,
            "missing_optional_assets": missing_optional,
            "notes": notes,
        }

    if runtime_state.get("startup_error"):
        features["try_on"]["status"] = STATUS_UNAVAILABLE
        features["try_on"]["notes"].append(f"Runtime startup error: {runtime_state['startup_error']}")

    if runtime_state.get("gfpgan_ready") is False:
        features["try_on"]["notes"].append(
            "Face restoration is disabled because GFPGAN could not be initialized."
        )

    warnings: list[str] = []
    if (models_root / "LLM").exists() and not (models_root / "LLM").is_symlink():
        warnings.append("Legacy uppercase namespace detected: LLM")
    if (models_root / "settings.json").exists():
        warnings.append("Legacy app settings file detected in model vault: settings.json")
    if not (models_root / ".cache" / "huggingface").exists():
        warnings.append("Missing shared Hugging Face cache directory: .cache/huggingface")

    status_counts = {STATUS_READY: 0, "degraded": 0, STATUS_UNAVAILABLE: 0}
    for feature in features.values():
        status_counts[feature["status"]] += 1

    return {
        "models_root": str(models_root),
        "assets": assets,
        "features": features,
        "summary": {
            "ready": status_counts[STATUS_READY],
            "degraded": status_counts["degraded"],
            "unavailable": status_counts[STATUS_UNAVAILABLE],
        },
        "warnings": warnings,
    }


def feature_is_available(report: dict[str, Any], feature_key: str) -> bool:
    return report["features"][feature_key]["status"] == STATUS_READY


def feature_status_message(report: dict[str, Any], feature_key: str) -> str:
    feature = report["features"][feature_key]
    notes = feature.get("notes") or []
    if not notes:
        return f"{feature['label']}: {feature['status']}"
    return f"{feature['label']}: {feature['status']} | " + " ".join(notes)


def render_capability_markdown(report: dict[str, Any], *, feature_keys: tuple[str, ...] | None = None) -> str:
    selected = feature_keys or tuple(feature["key"] for feature in FEATURES)
    lines = ["## Runtime Capabilities"]
    for feature_key in selected:
        feature = report["features"][feature_key]
        lines.append(f"- `{feature['label']}`: `{feature['status']}`")
        for note in feature["notes"][:2]:
            lines.append(f"  {note}")
    if report["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
    return "\n".join(lines)
