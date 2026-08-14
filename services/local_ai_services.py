from __future__ import annotations

import csv
import json
import math
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps


LOCAL_AI_CONTRACT_VERSION = "local-ai-services-v1"
LOCAL_AI_JOB_SCHEMA_VERSION = 1
LOCAL_AI_ARTIFACT_SCHEMA_VERSION = 1
UTC = timezone.utc


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def safe_id(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value).strip().lower())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    if not cleaned:
        raise ValueError("invalid_id")
    return cleaned[:96]


def local_ai_root(app_root: Path) -> Path:
    root = app_root / ".runtime" / "local_ai"
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifact_root(app_root: Path) -> Path:
    root = local_ai_root(app_root) / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def jobs_root(app_root: Path) -> Path:
    root = local_ai_root(app_root) / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass(frozen=True)
class LocalAiService:
    service_id: str
    label: str
    milestone: str
    required_model_packs: tuple[str, ...]
    input_schema: str
    output_schema: str
    ui_required: bool = False


@dataclass(frozen=True)
class ModelPack:
    pack_id: str
    label: str
    required_paths: tuple[str, ...]
    optional_paths: tuple[str, ...] = ()


MODEL_PACKS: tuple[ModelPack, ...] = (
    ModelPack("pillow_core", "Pillow deterministic image operations", (".cache/huggingface",)),
    ModelPack("segmentation_optional", "Optional local segmentation models", ("processors/catvton-segmentation",)),
    ModelPack("inpainting_optional", "Optional local inpainting models", ("checkpoints/sd15-inpainting",)),
    ModelPack("upscale_optional", "Optional local upscaling/restoration models", ("processors/face-restoration",), optional_paths=("processors/upscalers",)),
    ModelPack("google_edge_mediapipe", "Google AI Edge MediaPipe models", ("processors/google-edge-mediapipe",)),
)


SERVICES: tuple[LocalAiService, ...] = (
    LocalAiService("garment_isolation", "Garment isolation", "Product Studio", ("pillow_core",), "GarmentIsolationJob", "LocalAiArtifact"),
    LocalAiService("product_photo_cleanup", "Product photo cleanup", "Product Studio", ("pillow_core",), "ProductCleanupJob", "LocalAiArtifactSet"),
    LocalAiService("brand_safety_analyzer", "Brand safety analyzer", "Quality", ("pillow_core",), "BrandSafetyJob", "BrandSafetyResult"),
    LocalAiService("tryon_quality_gate", "Try-on quality gate", "Quality", ("pillow_core",), "TryOnQualityGateJob", "TryOnQualityGate"),
    LocalAiService("local_inpainting_cleanup", "Local inpainting cleanup", "Editing", ("pillow_core",), "InpaintRepairJob", "LocalAiArtifact"),
    LocalAiService("campaign_variant_generator", "Campaign variant generator", "Variants", ("pillow_core",), "VariantSetJob", "LocalAiArtifactSet"),
    LocalAiService("event_social_still_builder", "Event social still builder", "Events", ("pillow_core",), "EventSocialStillJob", "LocalAiArtifactSet"),
    LocalAiService("synthetic_fixture_generator", "Synthetic fixture generator", "Validation", ("pillow_core",), "SyntheticFixtureJob", "SyntheticFixtureSet"),
    LocalAiService("local_ai_services_console", "Local AI services console", "Operator UX", ("pillow_core",), "OperatorConsoleContract", "GDSOperatorSurface", ui_required=True),
    LocalAiService("local_ai_service_reporting", "Local AI service reporting", "Analytics", ("pillow_core",), "ReportRequest", "LocalAiServiceReport"),
    LocalAiService("google_edge_analyzer", "Google AI Edge analyzer", "Quality", ("google_edge_mediapipe",), "GoogleEdgeAnalyzerJob", "GoogleEdgeAnalyzerResult"),
    LocalAiService("google_edge_tryon", "Google AI Edge virtual try-on overlay", "Product Studio", ("google_edge_mediapipe",), "GoogleEdgeTryOnJob", "LocalAiArtifact"),
)


def _path_status(models_root: Path, relative_path: str) -> dict[str, Any]:
    path = models_root / relative_path
    return {"path": str(path), "exists": path.exists(), "readable": path.exists() and path.is_dir() or path.is_file()}


def evaluate_model_packs(models_root: Path) -> dict[str, Any]:
    packs: dict[str, Any] = {}
    for pack in MODEL_PACKS:
        required = [_path_status(models_root, item) for item in pack.required_paths]
        optional = [_path_status(models_root, item) for item in pack.optional_paths]
        ready = all(item["exists"] for item in required)
        packs[pack.pack_id] = {
            "packId": pack.pack_id,
            "label": pack.label,
            "status": "ready" if ready else "unavailable",
            "required": required,
            "optional": optional,
            "zeroExternalCost": True,
        }
    return {"contractVersion": LOCAL_AI_CONTRACT_VERSION, "generatedAt": now_iso(), "modelPacks": packs}


def service_registry(models_root: Path) -> dict[str, Any]:
    packs = evaluate_model_packs(models_root)["modelPacks"]
    services = []
    for service in SERVICES:
        missing = [pack for pack in service.required_model_packs if packs.get(pack, {}).get("status") != "ready"]
        services.append(
            {
                "serviceId": service.service_id,
                "label": service.label,
                "milestone": service.milestone,
                "version": 1,
                "active": True,
                "zeroExternalCost": True,
                "requiredModelPacks": list(service.required_model_packs),
                "missingModelPacks": missing,
                "status": "ready" if not missing else "unavailable",
                "inputSchema": service.input_schema,
                "outputSchema": service.output_schema,
                "uiRequired": service.ui_required,
                "gdsRequired": service.ui_required,
            }
        )
    return {"contractVersion": LOCAL_AI_CONTRACT_VERSION, "generatedAt": now_iso(), "services": services}


def _resolve_input_path(value: str) -> Path:
    path = Path(str(value)).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"missing_input:{path}")
    return path


def _open_image(path: Path) -> Image.Image:
    return ImageOps.exif_transpose(Image.open(path)).convert("RGBA")


def _artifact_dir(app_root: Path, service_id: str, job_id: str) -> Path:
    path = artifact_root(app_root) / safe_id(service_id) / safe_id(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_metadata(path: Path, payload: dict[str, Any]) -> Path:
    meta = path.with_suffix(f"{path.suffix}.json")
    meta.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return meta


def _save_job_record(app_root: Path, payload: dict[str, Any]) -> None:
    path = jobs_root(app_root) / f"{safe_id(payload['jobId'])}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _image_metrics(image: Image.Image) -> dict[str, Any]:
    arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    gray = arr.mean(axis=2)
    return {
        "width": image.width,
        "height": image.height,
        "meanLuma": float(gray.mean()),
        "stdLuma": float(gray.std()),
        "hasAlpha": "A" in image.getbands(),
    }


def _simple_subject_mask(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    arr = np.asarray(rgba)
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3].astype(np.int16)
    corners = np.concatenate([rgb[:10, :10].reshape(-1, 3), rgb[:10, -10:].reshape(-1, 3), rgb[-10:, :10].reshape(-1, 3), rgb[-10:, -10:].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
    mask = ((dist > 28) | (alpha < 250)).astype(np.uint8) * 255
    pil = Image.fromarray(mask, mode="L")
    return pil.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.GaussianBlur(1.2)).point(lambda p: 255 if p > 32 else 0)


def _crop_to_mask(image: Image.Image, mask: Image.Image) -> tuple[Image.Image, Image.Image]:
    bbox = mask.getbbox()
    if not bbox:
        return image, mask
    return image.crop(bbox), mask.crop(bbox)


def _save_artifact(image: Image.Image, output_path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    metadata_path = _write_metadata(output_path, metadata)
    return {"path": str(output_path), "metadataPath": str(metadata_path), "metrics": _image_metrics(image)}


def garment_isolation(app_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = payload.get("jobId") or f"garment-{int(time.time())}"
    source = _resolve_input_path(payload["sourceImagePath"])
    image = _open_image(source)
    mask = _simple_subject_mask(image)
    cropped, cropped_mask = _crop_to_mask(image, mask)
    isolated = cropped.copy()
    isolated.putalpha(cropped_mask)
    out_dir = _artifact_dir(app_root, "garment_isolation", job_id)
    artifact = _save_artifact(
        isolated,
        out_dir / "garment.png",
        {"schemaVersion": LOCAL_AI_ARTIFACT_SCHEMA_VERSION, "serviceId": "garment_isolation", "jobId": job_id, "sourcePath": str(source), "qualityState": "review"},
    )
    mask_artifact = _save_artifact(cropped_mask.convert("L"), out_dir / "mask.png", {"schemaVersion": LOCAL_AI_ARTIFACT_SCHEMA_VERSION, "serviceId": "garment_isolation", "jobId": job_id, "kind": "mask"})
    return {"jobId": job_id, "status": "completed", "artifact": artifact, "mask": mask_artifact, "qualityState": "review"}


def product_photo_cleanup(app_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = payload.get("jobId") or f"cleanup-{int(time.time())}"
    source = _resolve_input_path(payload["inputImagePath"])
    image = _open_image(source)
    background = str(payload.get("backgroundMode") or "white")
    ratios = payload.get("outputRatios") or ["1:1", "4:5", "9:16"]
    mask = _simple_subject_mask(image)
    cleaned = image.copy()
    if background == "transparent":
        cleaned.putalpha(mask)
    else:
        canvas = Image.new("RGBA", image.size, (255, 255, 255, 255))
        canvas.alpha_composite(image)
        cleaned = canvas
    cleaned = ImageEnhance.Contrast(cleaned).enhance(1.04)
    out_dir = _artifact_dir(app_root, "product_photo_cleanup", job_id)
    artifacts = []
    for ratio in ratios:
        w, h = _parse_ratio(str(ratio))
        variant = ImageOps.pad(cleaned, (max(512, 400 * w), max(512, 400 * h)), color=(255, 255, 255, 0 if background == "transparent" else 255))
        artifacts.append(_save_artifact(variant, out_dir / f"cleanup_{safe_id(str(ratio))}.png", {"schemaVersion": LOCAL_AI_ARTIFACT_SCHEMA_VERSION, "serviceId": "product_photo_cleanup", "jobId": job_id, "ratio": ratio}))
    return {"jobId": job_id, "status": "completed", "artifacts": artifacts}


def _parse_ratio(value: str) -> tuple[int, int]:
    if ":" not in value:
        return (1, 1)
    left, right = value.split(":", 1)
    try:
        return max(1, int(left)), max(1, int(right))
    except Exception:
        return (1, 1)


def brand_safety_analyzer(app_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    source = _open_image(_resolve_input_path(payload["sourceImagePath"])).convert("RGB")
    output = _open_image(_resolve_input_path(payload["outputImagePath"])).convert("RGB")
    source_small = ImageOps.fit(source, (256, 256))
    output_small = ImageOps.fit(output, (256, 256))
    diff = ImageChops.difference(source_small, output_small).convert("L")
    arr = np.asarray(diff, dtype=np.float32)
    similarity = max(0.0, 1.0 - float(arr.mean()) / 255.0)
    color_delta = abs(_image_metrics(source)["meanLuma"] - _image_metrics(output)["meanLuma"]) / 255.0
    score = max(0.0, min(1.0, (similarity * 0.7) + ((1.0 - color_delta) * 0.3)))
    status = "pass" if score >= 0.78 else "warn" if score >= 0.58 else "fail"
    result = {
        "serviceId": "brand_safety_analyzer",
        "schemaVersion": 1,
        "score": round(score, 4),
        "status": status,
        "checks": [
            {"id": "visual_similarity", "score": round(similarity, 4), "reason": "Source and output visual similarity"},
            {"id": "luma_delta", "score": round(1.0 - color_delta, 4), "reason": "Brightness/color drift guard"},
        ],
    }
    _save_job_record(app_root, {"jobId": payload.get("jobId") or f"brand-{int(time.time())}", "serviceId": "brand_safety_analyzer", "status": "completed", "result": result, "updatedAt": now_iso()})
    return result


def tryon_quality_gate(app_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Score a finished try-on render and recommend accept / review / rerun / reject.

    Three layers, worst outcome wins: cheap image metrics, the brand-safety comparison
    against the source, and — when the MediaPipe pack is present — pose validation of
    the source photo, which catches "the render is fine but the input was never going
    to work".

    Pose validation is best-effort by design: any failure there is logged and skipped
    rather than raised, so a missing model pack degrades the gate instead of breaking
    it. That also means a silent skip looks identical to a pass in the result, so check
    `checks.googleEdgeAnalysis` for null before trusting that a pose was validated.
    """
    output = _open_image(_resolve_input_path(payload["outputImagePath"]))
    metrics = _image_metrics(output)
    failures = []
    warnings = []
    if metrics["width"] < 512 or metrics["height"] < 512:
        failures.append("resolution_below_contract")
    if metrics["meanLuma"] < 4:
        failures.append("near_black_output")
    if metrics["stdLuma"] < 8:
        warnings.append("low_tonal_variance")
    brand = None
    if payload.get("sourceImagePath"):
        brand = brand_safety_analyzer(app_root, {"sourceImagePath": payload["sourceImagePath"], "outputImagePath": payload["outputImagePath"]})
        if brand["status"] == "fail":
            warnings.append("brand_safety_failed")

    edge_analysis = None
    if payload.get("sourceImagePath"):
        try:
            # Readiness is vault-relative, so this must be the models root: passing
            # app_root here made the pack read "unavailable" and silently skipped
            # pose validation for every job.
            from model_paths import get_models_root

            report = evaluate_model_packs(get_models_root())
            if report["modelPacks"].get("google_edge_mediapipe", {}).get("status") == "ready":
                edge_analysis = google_edge_analyzer(app_root, {"sourceImagePath": payload["sourceImagePath"]})
                if edge_analysis["status"] == "fail":
                    failures.append("pose_validation_failed")
                elif edge_analysis["status"] == "warn":
                    warnings.append("pose_validation_warning")
        except Exception as e:
            print(f"[warning] Google edge analyzer skipped in quality gate: {e}")

    status = "fail" if failures else "warn" if warnings else "pass"
    recommendation = "manual_reject" if failures else "rerun" if "brand_safety_failed" in warnings else "review"
    return {"serviceId": "tryon_quality_gate", "status": status, "recommendation": recommendation, "checks": {"failures": failures, "warnings": warnings, "metrics": metrics, "brandSafety": brand, "googleEdgeAnalysis": edge_analysis}}


def google_edge_analyzer(app_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Judge whether a source photo is a usable try-on subject, using MediaPipe landmarks.

    Screens for the poses that make CatVTON produce garbage — raised arms, a strongly
    tilted or turned body, no detectable person — so a bad input can be rejected before
    a multi-minute render rather than after it. Checks return "warn" rather than "fail"
    where the render usually survives (shoulder tilt), and the thresholds are empirical,
    normalized to image size: landmark coordinates are 0..1, so 0.1 of shoulder tilt is
    a tenth of image height, not pixels.

    Landmark indices are MediaPipe Pose's fixed layout: 11/12 shoulders, 13/14 elbows,
    15/16 wrists, 23/24 hips.

    Raises ImportError if mediapipe is absent and FileNotFoundError if the .task model
    files are missing from the vault — both are provisioning faults, not bad input, so
    they are not folded into the pass/fail result.
    """
    job_id = payload.get("jobId") or f"edge-{int(time.time())}"
    source_path = _resolve_input_path(payload["sourceImagePath"])

    try:
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
    except ImportError as e:
        raise ImportError(f"mediapipe_not_installed: {e}")

    from model_paths import get_models_root
    models_root = get_models_root()
    pose_model_path = models_root / "processors" / "google-edge-mediapipe" / "pose_landmarker_full.task"
    face_model_path = models_root / "processors" / "google-edge-mediapipe" / "face_landmarker.task"

    if not pose_model_path.exists() or not face_model_path.exists():
        raise FileNotFoundError("missing_mediapipe_task_files")

    image = _open_image(source_path)
    img_rgb = np.array(image.convert("RGB"))
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)

    pose_options = vision.PoseLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(pose_model_path)),
        output_segmentation_masks=False
    )

    pose_passed = False
    pose_checks = []
    pose_metrics = {}

    with vision.PoseLandmarker.create_from_options(pose_options) as pose_landmarker:
        pose_result = pose_landmarker.detect(mp_image)
        if pose_result.pose_landmarks:
            pose_passed = True
            landmarks = pose_result.pose_landmarks[0]

            ls = landmarks[11]
            rs = landmarks[12]
            le = landmarks[13]
            re = landmarks[14]
            lw = landmarks[15]
            rw = landmarks[16]
            lh = landmarks[23]
            rh = landmarks[24]

            arms_down = (le.y > ls.y) and (re.y > rs.y) and (lw.y > ls.y) and (rw.y > rs.y)
            pose_checks.append({
                "id": "arms_down",
                "status": "pass" if arms_down else "fail",
                "reason": "Arms are pointing downwards" if arms_down else "Arms or hands are raised"
            })

            shoulder_tilt = abs(ls.y - rs.y)
            shoulder_aligned = shoulder_tilt < 0.1
            pose_checks.append({
                "id": "shoulder_alignment",
                "status": "pass" if shoulder_aligned else "warn",
                "reason": f"Shoulder tilt is {shoulder_tilt:.4f}"
            })

            torso_valid = (lh.y > ls.y) and (rh.y > rs.y)
            pose_checks.append({
                "id": "torso_validity",
                "status": "pass" if torso_valid else "fail",
                "reason": "Hips are below shoulders" if torso_valid else "Invalid torso structure"
            })

            nose = landmarks[0]
            left_x = min(ls.x, rs.x)
            right_x = max(ls.x, rs.x)
            facing_front = left_x < nose.x < right_x
            pose_checks.append({
                "id": "facing_camera",
                "status": "pass" if facing_front else "warn",
                "reason": "Nose is positioned between shoulders" if facing_front else "Nose is out of center"
            })

            pose_metrics = {
                "shoulderTilt": float(shoulder_tilt),
                "leftShoulder": {"x": float(ls.x), "y": float(ls.y), "z": float(ls.z)},
                "rightShoulder": {"x": float(rs.x), "y": float(rs.y), "z": float(rs.z)},
                "leftElbow": {"x": float(le.x), "y": float(le.y)},
                "rightElbow": {"x": float(re.x), "y": float(re.y)},
                "leftWrist": {"x": float(lw.x), "y": float(lw.y)},
                "rightWrist": {"x": float(rw.x), "y": float(rw.y)},
            }
        else:
            pose_checks.append({
                "id": "pose_detected",
                "status": "fail",
                "reason": "No body pose was detected"
            })

    face_options = vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(face_model_path)),
        num_faces=1
    )

    face_passed = False
    face_checks = []

    with vision.FaceLandmarker.create_from_options(face_options) as face_landmarker:
        face_result = face_landmarker.detect(mp_image)
        if face_result.face_landmarks:
            face_passed = True
            face_checks.append({
                "id": "face_detected",
                "status": "pass",
                "reason": "Face detected successfully"
            })
        else:
            face_checks.append({
                "id": "face_detected",
                "status": "warn",
                "reason": "No face detected (image might be neck-down cropped)"
            })

    all_checks = pose_checks + face_checks
    has_fail = any(c["status"] == "fail" for c in all_checks)
    has_warn = any(c["status"] == "warn" for c in all_checks)
    status = "fail" if has_fail else "warn" if has_warn else "pass"

    result = {
        "serviceId": "google_edge_analyzer",
        "schemaVersion": 1,
        "status": status,
        "posePassed": pose_passed,
        "facePassed": face_passed,
        "checks": all_checks,
        "metrics": {
            **pose_metrics,
            "numFacesDetected": 1 if face_passed else 0
        }
    }

    _save_job_record(app_root, {
        "jobId": job_id,
        "serviceId": "google_edge_analyzer",
        "status": "completed",
        "result": result,
        "updatedAt": now_iso()
    })

    return result


def google_edge_tryon(app_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Composite the garment onto the person by pose landmarks — a preview, not a render.

    No diffusion: the garment is scaled and placed from the shoulder width and torso
    height MediaPipe reports, which takes a second instead of minutes. Use it for
    instant framing feedback (does the garment land in the right place?), never as a
    substitute for the CatVTON path — it cannot drape, shade, or occlude.

    The google_edge_* payload keys nudge the placement (width/height scale, x/y offset,
    tilt) and exist because landmark-derived geometry is systematically off for some
    garment cuts; they are the calibration knob, not tuning for its own sake.

    Raises ValueError("no_body_pose_detected") when there is no person to place against.
    """
    job_id = payload.get("jobId") or f"tryon-{int(time.time())}"
    person_path = _resolve_input_path(payload["personImagePath"])
    garment_path = _resolve_input_path(payload["garmentImagePath"])
    output_path = Path(payload["outputImagePath"]).expanduser().resolve()

    try:
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
    except ImportError as e:
        raise ImportError(f"mediapipe_not_installed: {e}")

    from model_paths import get_models_root
    models_root = get_models_root()
    pose_model_path = models_root / "processors" / "google-edge-mediapipe" / "pose_landmarker_full.task"

    if not pose_model_path.exists():
        raise FileNotFoundError("missing_mediapipe_pose_task_file")

    person_img = _open_image(person_path)
    garment_img = _open_image(garment_path)

    img_rgb = np.array(person_img.convert("RGB"))
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)

    pose_options = vision.PoseLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(pose_model_path)),
        output_segmentation_masks=False
    )

    with vision.PoseLandmarker.create_from_options(pose_options) as pose_landmarker:
        pose_result = pose_landmarker.detect(mp_image)
        if not pose_result.pose_landmarks:
            raise ValueError("no_body_pose_detected")

        landmarks = pose_result.pose_landmarks[0]
        ls = landmarks[11]
        rs = landmarks[12]
        lh = landmarks[23]
        rh = landmarks[24]

        img_w, img_h = person_img.size

        shoulder_dist = math.sqrt((ls.x - rs.x)**2 + (ls.y - rs.y)**2)
        shoulder_center_y = (ls.y + rs.y) / 2
        hip_center_y = (lh.y + rh.y) / 2
        torso_h = abs(shoulder_center_y - hip_center_y)

        def _get_float(key: str, default: float) -> float:
            val = payload.get(key)
            if val is None:
                return default
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        width_scale = _get_float("google_edge_width_scale", 1.55)
        height_scale = _get_float("google_edge_height_scale", 1.35)
        offset_x = _get_float("google_edge_offset_x", 0.0)
        offset_y = _get_float("google_edge_offset_y", 0.15)
        tilt_adjustment = _get_float("google_edge_tilt_adjustment", 0.0)

        target_width = int(shoulder_dist * img_w * width_scale)
        target_height = int(torso_h * img_h * height_scale)

        if target_width <= 0 or target_height <= 0:
            raise ValueError("invalid_torso_dimensions")

        bbox = garment_img.getbbox()
        if bbox:
            garment_img = garment_img.crop(bbox)

        garment_scaled = garment_img.resize((target_width, target_height), Image.Resampling.LANCZOS)

        angle_rad = math.atan2(ls.y - rs.y, ls.x - rs.x)
        angle_deg = math.degrees(angle_rad) + tilt_adjustment

        garment_rotated = garment_scaled.rotate(-angle_deg, expand=True, resample=Image.Resampling.BICUBIC)

        shoulder_center_x = (ls.x + rs.x) / 2 * img_w
        paste_center_x = shoulder_center_x + (offset_x * shoulder_dist * img_w)
        paste_center_y = (shoulder_center_y + offset_y * torso_h) * img_h

        paste_x = int(paste_center_x - garment_rotated.width / 2)
        paste_y = int(paste_center_y - garment_rotated.height / 2)

        overlay = Image.new("RGBA", person_img.size, (0, 0, 0, 0))
        overlay.paste(garment_rotated, (paste_x, paste_y), mask=garment_rotated)

        result_img = Image.alpha_composite(person_img.convert("RGBA"), overlay)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() in {".jpg", ".jpeg"}:
            result_img.convert("RGB").save(output_path, "JPEG", quality=95)
        else:
            result_img.save(output_path, "PNG")

        metadata = {
            "schemaVersion": LOCAL_AI_ARTIFACT_SCHEMA_VERSION,
            "serviceId": "google_edge_tryon",
            "jobId": job_id,
            "personImagePath": str(person_path),
            "garmentImagePath": str(garment_path),
            "outputImagePath": str(output_path),
            "metrics": {
                "shoulderDistance": float(shoulder_dist),
                "torsoHeight": float(torso_h),
                "shoulderTiltAngle": float(angle_deg),
                "widthScale": float(width_scale),
                "heightScale": float(height_scale),
                "offsetX": float(offset_x),
                "offsetY": float(offset_y),
                "tiltAdjustment": float(tilt_adjustment)
            }
        }
        metadata_path = _write_metadata(output_path, metadata)

        return {
            "jobId": job_id,
            "status": "completed",
            "artifact": {
                "path": str(output_path),
                "metadataPath": str(metadata_path),
                "metrics": _image_metrics(result_img)
            }
        }


def local_inpainting_cleanup(app_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = payload.get("jobId") or f"repair-{int(time.time())}"
    image = _open_image(_resolve_input_path(payload["inputImagePath"]))
    mask = Image.open(_resolve_input_path(payload["maskImagePath"])).convert("L") if payload.get("maskImagePath") else _simple_subject_mask(image).filter(ImageFilter.FIND_EDGES)
    blurred = image.filter(ImageFilter.GaussianBlur(8))
    repaired = Image.composite(blurred, image, mask)
    out_dir = _artifact_dir(app_root, "local_inpainting_cleanup", job_id)
    artifact = _save_artifact(repaired, out_dir / "repaired.png", {"schemaVersion": LOCAL_AI_ARTIFACT_SCHEMA_VERSION, "serviceId": "local_inpainting_cleanup", "jobId": job_id, "parentPath": payload["inputImagePath"]})
    return {"jobId": job_id, "status": "completed", "artifact": artifact}


def campaign_variant_generator(app_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload.setdefault("outputRatios", ["1:1", "4:5", "9:16", "16:9"])
    payload["inputImagePath"] = payload.get("sourceImagePath") or payload.get("inputImagePath")
    return {"serviceId": "campaign_variant_generator", **product_photo_cleanup(app_root, payload)}


def event_social_still_builder(app_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = payload.get("jobId") or f"event-{int(time.time())}"
    source = _open_image(_resolve_input_path(payload["sourceImagePath"]))
    event_name = str(payload.get("eventName") or "Event")
    canvas = ImageOps.pad(source, (1200, 1600), color=(18, 18, 18, 255))
    band = Image.new("RGBA", (1200, 170), (255, 255, 255, 230))
    canvas.alpha_composite(band, (0, 1430))
    out_dir = _artifact_dir(app_root, "event_social_still_builder", job_id)
    artifact = _save_artifact(canvas, out_dir / f"{safe_id(event_name)}.png", {"schemaVersion": LOCAL_AI_ARTIFACT_SCHEMA_VERSION, "serviceId": "event_social_still_builder", "jobId": job_id, "eventName": event_name})
    return {"jobId": job_id, "status": "completed", "artifacts": [artifact]}


def synthetic_fixture_generator(app_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = payload.get("jobId") or f"fixture-{int(time.time())}"
    out_dir = _artifact_dir(app_root, "synthetic_fixture_generator", job_id)
    artifacts = []
    scenarios = payload.get("scenarios") or ["garment", "person", "background"]
    for index, scenario in enumerate(scenarios):
        width, height = 768, 1024
        arr = np.zeros((height, width, 3), dtype=np.uint8)
        x = np.linspace(0, 255, width, dtype=np.uint8)
        y = np.linspace(0, 255, height, dtype=np.uint8)
        arr[:, :, 0] = x
        arr[:, :, 1] = y[:, None]
        arr[:, :, 2] = (index * 80) % 255
        image = Image.fromarray(arr, "RGB")
        artifacts.append(_save_artifact(image, out_dir / f"{safe_id(str(scenario))}.png", {"schemaVersion": LOCAL_AI_ARTIFACT_SCHEMA_VERSION, "serviceId": "synthetic_fixture_generator", "jobId": job_id, "scenario": scenario}))
    return {"jobId": job_id, "status": "completed", "artifacts": artifacts}


def local_ai_report(app_root: Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = []
    for path in jobs_root(app_root).glob("*.json"):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    counts: dict[str, dict[str, Any]] = {}
    for row in rows:
        service = row.get("serviceId") or "unknown"
        status = row.get("status") or "unknown"
        item = counts.setdefault(service, {"serviceId": service, "jobCount": 0, "successCount": 0, "failureCount": 0, "estimatedExternalCostAvoided": 0.0})
        item["jobCount"] += 1
        if status == "completed":
            item["successCount"] += 1
            item["estimatedExternalCostAvoided"] += 0.08
        elif status == "failed":
            item["failureCount"] += 1
    return {"contractVersion": LOCAL_AI_CONTRACT_VERSION, "generatedAt": now_iso(), "services": list(counts.values()), "totalJobs": len(rows)}


RUNNERS = {
    "garment_isolation": garment_isolation,
    "product_photo_cleanup": product_photo_cleanup,
    "brand_safety_analyzer": brand_safety_analyzer,
    "tryon_quality_gate": tryon_quality_gate,
    "google_edge_analyzer": google_edge_analyzer,
    "google_edge_tryon": google_edge_tryon,
    "local_inpainting_cleanup": local_inpainting_cleanup,
    "campaign_variant_generator": campaign_variant_generator,
    "event_social_still_builder": event_social_still_builder,
    "synthetic_fixture_generator": synthetic_fixture_generator,
    "local_ai_service_reporting": local_ai_report,
}


def run_local_ai_service(app_root: Path, service_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    service_id = safe_id(service_id)
    if service_id not in RUNNERS:
        raise ValueError(f"unsupported_service:{service_id}")
    started = now_iso()
    job_id = payload.get("jobId") or f"{service_id}-{int(time.time())}"
    record = {"schemaVersion": LOCAL_AI_JOB_SCHEMA_VERSION, "jobId": job_id, "serviceId": service_id, "status": "running", "startedAt": started, "updatedAt": started}
    _save_job_record(app_root, record)
    try:
        result = RUNNERS[service_id](app_root, {**payload, "jobId": job_id})
    except Exception as exc:
        failed = {**record, "status": "failed", "error": {"message": str(exc)}, "updatedAt": now_iso()}
        _save_job_record(app_root, failed)
        raise
    completed = {**record, "status": "completed", "result": result, "updatedAt": now_iso()}
    _save_job_record(app_root, completed)
    return result


def export_report_csv(app_root: Path, output_path: Path) -> Path:
    report = local_ai_report(app_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["serviceId", "jobCount", "successCount", "failureCount", "estimatedExternalCostAvoided"])
        writer.writeheader()
        for row in report["services"]:
            writer.writerow(row)
    return output_path
