from __future__ import annotations

from typing import Any


SUPPORTED_JOB_SCHEMA_VERSIONS = {None, 1}
SUPPORTED_SUIT_SCHEMA_VERSIONS = {None, 1}
PROCESSING_PROFILE_GENERIC = "generic"
PROCESSING_PROFILE_MOTOGP = "motogp_leather_magic"
PROCESSING_PROFILES = {PROCESSING_PROFILE_GENERIC, PROCESSING_PROFILE_MOTOGP}
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


def normalize_processing_profile(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"motogp", "motogp_leather_magic", "motogp-leather-magic"}:
        return PROCESSING_PROFILE_MOTOGP
    return PROCESSING_PROFILE_GENERIC


def validate_job_document(job: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    schema_version = job.get("schemaVersion")
    if schema_version not in SUPPORTED_JOB_SCHEMA_VERSIONS:
        errors.append("unsupported_job_schema_version")
    if not str(job.get("jobId") or "").strip():
        errors.append("missing_job_id")
    status = str(job.get("status") or "").strip()
    if status and status not in JOB_STATUSES:
        errors.append("invalid_job_status")
    source = job.get("source") or {}
    if not str(source.get("imageUrl") or "").strip():
        errors.append("missing_source_image_url")
    if not str(source.get("submissionId") or "").strip():
        errors.append("missing_submission_id")
    request = job.get("request") or {}
    if not str(request.get("leatherSuitId") or "").strip():
        errors.append("missing_leather_suit_id")
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
    if schema_version not in SUPPORTED_SUIT_SCHEMA_VERSIONS:
        errors.append("unsupported_suit_schema_version")
    if not str(suit.get("leatherSuitId") or "").strip():
        errors.append("missing_leather_suit_id")
    if suit.get("active") is not True:
        errors.append("inactive_suit")
    if not any(str(suit.get(key) or "").strip() for key in ("sourceImageUrl", "imageUrl", "previewUrl", "assetRelativePath", "assetKey")):
        errors.append("missing_suit_asset_reference")
    return errors
