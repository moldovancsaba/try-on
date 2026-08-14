"""Queue health, provider circuit breaking, and failure classification for the worker.

Everything here is advisory state the worker consults before and after each job:
whether a provider is in cooldown, whether the queue is under backpressure, and which
taxonomy bucket a failure belongs in. State lives in JSON files under `.runtime/`
rather than Atlas, because it describes this machine, not the shared queue.

Operator-facing behavior and the CLI over it: docs/TRYON_CRITICAL_INFRASTRUCTURE.md.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc
INFRA_CONTRACT_VERSION = "2026.06-critical-infra-v1"
PROVIDER_METRICS_SCHEMA_VERSION = 1
RECONCILIATION_SCHEMA_VERSION = 1
CANARY_SCHEMA_VERSION = 1

FAILURE_TAXONOMY: dict[str, dict[str, str]] = {
    "timeout": {"label": "Provider or network timeout", "recommendedAction": "Retry once; after repeated timeout leave failed and review provider latency."},
    "provider_error": {"label": "External provider error", "recommendedAction": "Check provider health, credentials, and circuit-breaker state."},
    "validation_error": {"label": "Invalid job or input contract", "recommendedAction": "Fix Camera job/suit data before replay."},
    "upload_error": {"label": "Image publication upload error", "recommendedAction": "Verify media host key, quota, and network."},
    "callback_error": {"label": "Camera completion callback error", "recommendedAction": "Run reconciliation and replay callback if safe."},
    "operator_cancel": {"label": "Operator cancellation", "recommendedAction": "Retry only if the operator intended to resume it."},
    "local_runtime_error": {"label": "Local try-on runtime error", "recommendedAction": "Check app health, model readiness, and worker logs."},
    "unknown": {"label": "Unknown failure", "recommendedAction": "Inspect redacted worker event and job metadata."},
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def parse_bool(value: str | None, fallback: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return fallback
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_int(value: str | None, fallback: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(str(value or "").strip())
    except Exception:
        parsed = fallback
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


@dataclass(frozen=True)
class QueueBackpressurePolicy:
    enabled: bool = True
    max_ready_jobs: int = 50
    max_oldest_ready_age_seconds: int = 3600


@dataclass(frozen=True)
class ProviderPolicy:
    provider: str
    timeout_seconds: int
    failure_threshold: int = 3
    slow_threshold_seconds: int = 240
    cooldown_seconds: int = 900
    daily_request_limit: int = 500
    concurrency_limit: int = 1


def classify_failure_category(code: str | None, message: str | None) -> str:
    """Return the FAILURE_TAXONOMY key for a failure, "unknown" if nothing matches.

    Substring matching over the code and message together, first match wins, so the
    order of the checks is the priority order: a timeout during an upload classifies
    as a timeout, not an upload error. This drives the operator's recommended action,
    not retry behavior — retryability is decided by classify_failure in the worker.
    """
    text = f"{code or ''} {message or ''}".lower()
    if "timeout" in text or "timed out" in text or "read timeout" in text:
        return "timeout"
    if "imgbb" in text or "upload" in text:
        return "upload_error"
    if "camera_completion" in text or "callback" in text:
        return "callback_error"
    if "operator_aborted" in text or "aborted" in text:
        return "operator_cancel"
    if any(token in text for token in ("segmind", "fal_", "provider", "api_failed", "status_failed")):
        return "provider_error"
    if any(token in text for token in ("invalid", "missing_", "allowlisted", "oversized", "schema", "contract")):
        return "validation_error"
    if "local_tryon" in text or "models" in text or "runtime" in text:
        return "local_runtime_error"
    return "unknown"


def failure_note(category: str, message: str | None = None) -> dict[str, str]:
    """Return the operator-facing note stored on a failed job.

    The message is truncated at the first "?" and to 240 characters before being
    persisted: provider errors routinely quote signed asset URLs, and the query string
    is where the credentials live. Keep that split if you touch this.
    """
    spec = FAILURE_TAXONOMY.get(category) or FAILURE_TAXONOMY["unknown"]
    safe_message = str(message or "").split("?", 1)[0][:240]
    return {
        "category": category,
        "label": spec["label"],
        "recommendedAction": spec["recommendedAction"],
        "message": safe_message,
    }


class ProviderCircuitBreaker:
    """Per-provider health tracking that opens a circuit after repeated failures.

    Counts requests, failures, timeouts, and slow calls per provider, and once a
    provider hits its policy's failure_threshold consecutive failures it is refused
    until the cooldown expires. State persists to `state_path` so a worker restart
    does not clear a circuit that a still-broken provider earned.

    Not thread-safe, and not shared between machines: each worker breaks its own
    circuits from its own observations.
    """

    def __init__(self, state_path: Path, policies: dict[str, ProviderPolicy]):
        self.state_path = state_path
        self.policies = policies
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"schemaVersion": PROVIDER_METRICS_SCHEMA_VERSION, "providers": {}}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"schemaVersion": PROVIDER_METRICS_SCHEMA_VERSION, "providers": {}}
        payload.setdefault("schemaVersion", PROVIDER_METRICS_SCHEMA_VERSION)
        payload.setdefault("providers", {})
        return payload

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")

    def provider_state(self, provider: str) -> dict[str, Any]:
        providers = self.state.setdefault("providers", {})
        state = providers.setdefault(
            provider,
            {
                "consecutiveFailures": 0,
                "circuitOpenUntil": None,
                "requestCount": 0,
                "successCount": 0,
                "failureCount": 0,
                "timeoutCount": 0,
                "slowCount": 0,
                "latenciesSeconds": [],
                "daily": {},
            },
        )
        return state

    def is_open(self, provider: str, *, at: datetime | None = None) -> bool:
        state = self.provider_state(provider)
        opened_until = parse_iso(state.get("circuitOpenUntil"))
        if not opened_until:
            return False
        current = at or datetime.now(UTC)
        if opened_until <= current:
            state["circuitOpenUntil"] = None
            state["consecutiveFailures"] = 0
            self.save()
            return False
        return True

    def assert_available(self, provider: str) -> None:
        if self.is_open(provider):
            until = self.provider_state(provider).get("circuitOpenUntil")
            raise RuntimeError(f"provider_circuit_open:{provider}:{until}")
        policy = self.policies.get(provider)
        if not policy or policy.daily_request_limit <= 0:
            return
        day = datetime.now(UTC).date().isoformat()
        daily = self.provider_state(provider).setdefault("daily", {})
        if int((daily.get(day) or {}).get("requests", 0)) >= policy.daily_request_limit:
            raise RuntimeError(f"provider_daily_limit_reached:{provider}:{policy.daily_request_limit}")

    def record_result(self, provider: str, *, ok: bool, latency_seconds: float, error: str | None = None) -> dict[str, Any]:
        policy = self.policies.get(provider) or ProviderPolicy(provider=provider, timeout_seconds=0)
        state = self.provider_state(provider)
        day = datetime.now(UTC).date().isoformat()
        daily = state.setdefault("daily", {}).setdefault(day, {"requests": 0, "successes": 0, "failures": 0})
        daily["requests"] = int(daily.get("requests", 0)) + 1
        state["requestCount"] = int(state.get("requestCount", 0)) + 1
        latencies = list(state.get("latenciesSeconds") or [])[-99:]
        latencies.append(round(float(latency_seconds), 3))
        state["latenciesSeconds"] = latencies
        state["lastLatencySeconds"] = round(float(latency_seconds), 3)
        state["lastResultAt"] = now_iso()
        if latency_seconds >= policy.slow_threshold_seconds:
            state["slowCount"] = int(state.get("slowCount", 0)) + 1
        if ok:
            state["successCount"] = int(state.get("successCount", 0)) + 1
            daily["successes"] = int(daily.get("successes", 0)) + 1
            state["consecutiveFailures"] = 0
            state["lastError"] = None
            state["circuitOpenUntil"] = None
        else:
            state["failureCount"] = int(state.get("failureCount", 0)) + 1
            daily["failures"] = int(daily.get("failures", 0)) + 1
            state["consecutiveFailures"] = int(state.get("consecutiveFailures", 0)) + 1
            state["lastError"] = str(error or "")[:300]
            if classify_failure_category(None, error) == "timeout":
                state["timeoutCount"] = int(state.get("timeoutCount", 0)) + 1
            if state["consecutiveFailures"] >= policy.failure_threshold:
                until = datetime.now(UTC) + timedelta(seconds=policy.cooldown_seconds)
                state["circuitOpenUntil"] = until.isoformat().replace("+00:00", "Z")
        self.save()
        return state

    def scorecard(self) -> dict[str, Any]:
        providers: dict[str, Any] = {}
        for provider, state in (self.state.get("providers") or {}).items():
            latencies = [float(value) for value in state.get("latenciesSeconds") or []]
            providers[provider] = {
                "requestCount": int(state.get("requestCount", 0)),
                "successCount": int(state.get("successCount", 0)),
                "failureCount": int(state.get("failureCount", 0)),
                "timeoutCount": int(state.get("timeoutCount", 0)),
                "slowCount": int(state.get("slowCount", 0)),
                "consecutiveFailures": int(state.get("consecutiveFailures", 0)),
                "circuitOpenUntil": state.get("circuitOpenUntil"),
                "lastLatencySeconds": state.get("lastLatencySeconds"),
                "p50LatencySeconds": round(statistics.median(latencies), 3) if latencies else None,
                "maxLatencySeconds": max(latencies) if latencies else None,
                "daily": state.get("daily") or {},
            }
        return {"schemaVersion": PROVIDER_METRICS_SCHEMA_VERSION, "generatedAt": now_iso(), "providers": providers}


def summarize_queue(jobs: Any, *, policy: QueueBackpressurePolicy) -> dict[str, Any]:
    """Return queue depth by status plus whether backpressure should hold off claiming.

    "Ready" means queued or retry_wait with no future nextAttemptAt — the jobs that
    could be claimed right now, which is a smaller number than the queued count and
    the one worth alarming on. Backpressure trips on ready depth or on the age of the
    oldest ready job; the age check is what catches a queue that is small but stuck.

    Runs four count_documents plus a find_one against Atlas, so call it once per loop
    rather than per job.
    """
    statuses = ["queued", "claimed", "processing", "uploading_result", "notifying_camera", "retry_wait", "done", "failed"]
    counts = {status: int(jobs.count_documents({"status": status})) for status in statuses}
    ready_filter = {
        "status": {"$in": ["queued", "retry_wait"]},
        "$or": [
            {"processing.nextAttemptAt": {"$exists": False}},
            {"processing.nextAttemptAt": None},
            {"processing.nextAttemptAt": {"$lte": now_iso()}},
        ],
    }
    ready_count = int(jobs.count_documents(ready_filter))
    oldest = jobs.find_one(ready_filter, sort=[("createdAt", 1)])
    oldest_at = parse_iso((oldest or {}).get("createdAt"))
    oldest_age = int((datetime.now(UTC) - oldest_at).total_seconds()) if oldest_at else 0
    pressure_reasons: list[str] = []
    if policy.enabled and ready_count > policy.max_ready_jobs:
        pressure_reasons.append("ready_depth_exceeded")
    if policy.enabled and oldest_age > policy.max_oldest_ready_age_seconds:
        pressure_reasons.append("oldest_ready_age_exceeded")
    return {
        "schemaVersion": INFRA_CONTRACT_VERSION,
        "generatedAt": now_iso(),
        "counts": counts,
        "readyCount": ready_count,
        "oldestReadyAgeSeconds": oldest_age,
        "backpressure": {
            "enabled": policy.enabled,
            "active": bool(pressure_reasons),
            "reasons": pressure_reasons,
            "maxReadyJobs": policy.max_ready_jobs,
            "maxOldestReadyAgeSeconds": policy.max_oldest_ready_age_seconds,
        },
    }


def reconcile_jobs(jobs: Any, *, limit: int = 200) -> dict[str, Any]:
    """Return jobs whose Atlas state is internally inconsistent, for operator review.

    Finds the four ways the publish→notify→done sequence can end up half-applied: a
    done job with no public url, an uploaded job Camera was never told about, an
    active job whose lease expired, and a failed job with no taxonomy category.

    Only the middle two are marked `safeReplay` — replaying them re-sends a callback
    that Camera de-duplicates. The other two need a human, because replaying them
    would either fabricate a result or fight a live worker. Read-only.
    """
    findings: list[dict[str, Any]] = []
    selectors = [
        ("done_missing_public_url", {"status": "done", "$or": [{"result.publicResultUrl": {"$exists": False}}, {"result.publicResultUrl": None}, {"result.publicResultUrl": ""}]}),
        ("uploaded_missing_camera_callback", {"result.publicResultUrl": {"$exists": True, "$nin": [None, ""]}, "processing.cameraNotifiedAt": {"$exists": False}, "status": {"$ne": "done"}}),
        ("active_expired_lease", {"status": {"$in": ["claimed", "processing", "uploading_result", "notifying_camera"]}, "processing.leaseExpiresAt": {"$lt": now_iso()}}),
        ("failed_missing_category", {"status": "failed", "$or": [{"error.category": {"$exists": False}}, {"error.category": None}, {"error.category": ""}]}),
    ]
    for finding_type, selector in selectors:
        for doc in jobs.find(selector, {"_id": 0, "jobId": 1, "status": 1, "stage": 1, "source.submissionId": 1, "result.publicResultUrl": 1, "processing.cameraNotifiedAt": 1, "error": 1}).limit(limit):
            findings.append({"type": finding_type, "jobId": doc.get("jobId"), "status": doc.get("status"), "stage": doc.get("stage"), "safeReplay": finding_type in {"uploaded_missing_camera_callback", "active_expired_lease"}, "detail": doc})
    return {"schemaVersion": RECONCILIATION_SCHEMA_VERSION, "generatedAt": now_iso(), "findingCount": len(findings), "findings": findings[:limit]}


def load_event_metrics(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    by_event = Counter(str(event.get("event") or "unknown") for event in events)
    failures = Counter()
    for event in events:
        if event.get("event") == "job_failed":
            details = event.get("details") or {}
            failures[classify_failure_category(details.get("code"), details.get("message"))] += 1
    return {"events": dict(by_event), "failureCategories": dict(failures)}


class Timer:
    def __enter__(self) -> "Timer":
        self.started = time.monotonic()
        self.elapsed = 0.0
        return self

    def __exit__(self, *_exc: object) -> None:
        self.elapsed = time.monotonic() - self.started
