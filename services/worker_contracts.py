from __future__ import annotations

from typing import Any


SUPPORTED_JOB_SCHEMA_VERSIONS = {1}
SUPPORTED_LEGACY_JOB_SCHEMA_VERSIONS = {None}
SUPPORTED_SUIT_SCHEMA_VERSIONS = {1}
SUPPORTED_LEGACY_SUIT_SCHEMA_VERSIONS = {None}
JOB_SCHEMA_VERSION = 1
SUIT_SCHEMA_VERSION = 1
PROCESSING_PROFILE_GENERIC = "generic"
PROCESSING_PROFILE_MOTOGP = "motogp_leather_magic"
PROCESSING_PROFILE_SEGMIND_IDM_VTON = "segmind_idm_vton"
PROCESSING_PROFILE_FAL_TRYON = "fal_tryon"
PROCESSING_PROFILES = {
    PROCESSING_PROFILE_GENERIC,
    PROCESSING_PROFILE_MOTOGP,
    PROCESSING_PROFILE_SEGMIND_IDM_VTON,
    PROCESSING_PROFILE_FAL_TRYON,
}
JOB_STATUSES = {
    "queued",
    "claimed",
    "processing",
    "uploading_result",
    "notifying_camera",
    "retry_wait",
    "done",
    "failed",
}
JOB_ACTIVE_STATUSES = {"claimed", "processing", "uploading_result", "notifying_camera"}
JOB_RETRYABLE_STATUSES = {"queued", "retry_wait", "failed"}
JOB_TERMINAL_STATUSES = {"done", "failed"}
JOB_STAGES = {
    "queued",
    "claimed",
    "downloading_input",
    "resolving_suit",
    "normalizing_job",
    "running_tryon",
    "uploading_result",
    "uploaded_result",
    "notifying_camera",
    "done",
    "failed",
    "aborted",
    "retry_requested",
}


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _clean_lower(value: Any) -> str:
    return _clean_string(value).lower()


def normalize_processing_profile(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"motogp", "motogp_leather_magic", "motogp-leather-magic"}:
        return PROCESSING_PROFILE_MOTOGP
    if raw in {"segmind", "segmind_idm_vton", "segmind-idm-vton", "idm_vton", "idm-vton"}:
        return PROCESSING_PROFILE_SEGMIND_IDM_VTON
    if raw in {"fal", "fal_tryon", "fal-fashn", "fashn", "fal-tryon", "fal_tryon", "fashn_tryon"}:
        return PROCESSING_PROFILE_FAL_TRYON
    return PROCESSING_PROFILE_GENERIC


def normalize_job_document(job: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(job)
    normalized["schemaVersion"] = normalized.get("schemaVersion") or JOB_SCHEMA_VERSION
    normalized["status"] = _clean_lower(normalized.get("status")) or "queued"
    normalized["stage"] = _clean_lower(normalized.get("stage")) or normalized["status"]

    source = dict(normalized.get("source") or {})
    request = dict(normalized.get("request") or {})
    processing = dict(normalized.get("processing") or {})
    error = dict(normalized.get("error") or {})

    if not source.get("submissionId"):
        source["submissionId"] = normalized.get("submissionId") or normalized.get("sourceSubmissionId") or normalized.get("cameraSubmissionId")
    if not source.get("imageUrl"):
        source["imageUrl"] = normalized.get("imageUrl") or normalized.get("sourceImageUrl") or normalized.get("photoUrl")
    if not source.get("cameraId"):
        source["cameraId"] = normalized.get("cameraId")

    if not request.get("leatherSuitId"):
        request["leatherSuitId"] = normalized.get("leatherSuitId") or normalized.get("suitId")
    if not request.get("setupId"):
        request["setupId"] = normalized.get("setupId")
    if not request.get("cameraId") and source.get("cameraId"):
        request["cameraId"] = source.get("cameraId")
    if request.get("processingProfile"):
        request["processingProfile"] = normalize_processing_profile(request.get("processingProfile"))

    if processing.get("attemptCount") is None:
        processing["attemptCount"] = 0

    normalized["source"] = source
    normalized["request"] = request
    normalized["processing"] = processing
    normalized["error"] = error
    return normalized


def normalize_suit_document(suit: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(suit)
    normalized["schemaVersion"] = normalized.get("schemaVersion") or SUIT_SCHEMA_VERSION
    if not normalized.get("leatherSuitId"):
        normalized["leatherSuitId"] = normalized.get("suitId") or normalized.get("id")
    if normalized.get("active") is None:
        normalized["active"] = True
    return normalized


def validate_job_document(job: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    schema_version = job.get("schemaVersion")
    if schema_version not in SUPPORTED_JOB_SCHEMA_VERSIONS and schema_version not in SUPPORTED_LEGACY_JOB_SCHEMA_VERSIONS:
        errors.append("unsupported_job_schema_version")
    if not str(job.get("jobId") or "").strip():
        errors.append("missing_job_id")
    status = str(job.get("status") or "").strip()
    if status and status not in JOB_STATUSES:
        errors.append("invalid_job_status")
    stage = str(job.get("stage") or "").strip()
    if stage and stage not in JOB_STAGES:
        errors.append("invalid_job_stage")
    source = job.get("source") or {}
    if not str(source.get("imageUrl") or "").strip():
        errors.append("missing_source_image_url")
    if not str(source.get("submissionId") or "").strip():
        errors.append("missing_submission_id")
    request = job.get("request") or {}
    if not str(request.get("leatherSuitId") or "").strip():
        errors.append("missing_leather_suit_id")
    # New optional fields used by the Mongo-backed setup resolver.
    setup_id = request.get("setupId")
    if setup_id is not None and not str(setup_id).strip():
        errors.append("invalid_setup_id")
    camera_id = request.get("cameraId")
    if camera_id is not None and not str(camera_id).strip():
        errors.append("invalid_camera_id")
    profile = normalize_processing_profile(request.get("processingProfile"))
    if request.get("processingProfile") not in (None, "", profile):
        # Unsupported names fall back to generic, but explicit unknown values should still be visible.
        if profile == PROCESSING_PROFILE_GENERIC and str(request.get("processingProfile") or "").strip():
            errors.append("invalid_processing_profile")
    processing = job.get("processing") or {}
    attempt_count = processing.get("attemptCount")
    if attempt_count is not None:
        try:
            if int(attempt_count) < 0:
                errors.append("invalid_attempt_count")
        except Exception:
            errors.append("invalid_attempt_count")
    return errors


def validate_suit_document(suit: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    schema_version = suit.get("schemaVersion")
    if schema_version not in SUPPORTED_SUIT_SCHEMA_VERSIONS and schema_version not in SUPPORTED_LEGACY_SUIT_SCHEMA_VERSIONS:
        errors.append("unsupported_suit_schema_version")
    if not str(suit.get("leatherSuitId") or "").strip():
        errors.append("missing_leather_suit_id")
    if suit.get("active") is not True:
        errors.append("inactive_suit")
    if not any(str(suit.get(key) or "").strip() for key in ("sourceImageUrl", "imageUrl", "previewUrl", "assetRelativePath", "assetKey")):
        errors.append("missing_suit_asset_reference")
    return errors
