from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from services.worker_infra import (
    ProviderCircuitBreaker,
    ProviderPolicy,
    QueueBackpressurePolicy,
    classify_failure_category,
    failure_note,
    reconcile_jobs,
    summarize_queue,
)


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def limit(self, value: int) -> list[dict[str, Any]]:
        return self.rows[:value]


class FakeJobs:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def count_documents(self, selector: dict[str, Any]) -> int:
        status = selector.get("status")
        if isinstance(status, str):
            return sum(1 for row in self.rows if row.get("status") == status)
        if isinstance(status, dict) and "$in" in status:
            return sum(1 for row in self.rows if row.get("status") in status["$in"])
        return len(self.rows)

    def find_one(self, _selector: dict[str, Any], sort: list[tuple[str, int]] | None = None) -> dict[str, Any] | None:
        rows = list(self.rows)
        if sort:
            rows.sort(key=lambda row: row.get(sort[0][0]) or "")
        return rows[0] if rows else None

    def find(self, selector: dict[str, Any], _projection: dict[str, Any]) -> FakeCursor:
        if selector.get("status") == "done":
            rows = [row for row in self.rows if row.get("status") == "done" and not (row.get("result") or {}).get("publicResultUrl")]
        elif selector.get("status") == "failed":
            rows = [row for row in self.rows if row.get("status") == "failed" and not (row.get("error") or {}).get("category")]
        elif "result.publicResultUrl" in selector:
            rows = [row for row in self.rows if (row.get("result") or {}).get("publicResultUrl") and not (row.get("processing") or {}).get("cameraNotifiedAt")]
        else:
            rows = []
        return FakeCursor(rows)


class WorkerInfraTests(unittest.TestCase):
    def test_failure_taxonomy_and_note(self) -> None:
        self.assertEqual(classify_failure_category("x", "read timeout=180"), "timeout")
        self.assertEqual(classify_failure_category("camera_completion_failed", "500"), "callback_error")
        note = failure_note("timeout", "read timeout?token=secret")
        self.assertEqual(note["category"], "timeout")
        self.assertNotIn("token", note["message"])

    def test_provider_circuit_opens_after_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            breaker = ProviderCircuitBreaker(
                Path(tmpdir) / "provider_metrics.json",
                {"segmind": ProviderPolicy("segmind", timeout_seconds=180, failure_threshold=2, cooldown_seconds=60)},
            )
            breaker.record_result("segmind", ok=False, latency_seconds=181, error="timeout")
            self.assertFalse(breaker.is_open("segmind"))
            breaker.record_result("segmind", ok=False, latency_seconds=182, error="timeout")
            self.assertTrue(breaker.is_open("segmind"))
            scorecard = breaker.scorecard()["providers"]["segmind"]
            self.assertEqual(scorecard["failureCount"], 2)
            self.assertEqual(scorecard["timeoutCount"], 2)

    def test_queue_pressure_summary(self) -> None:
        jobs = FakeJobs([
            {"jobId": "1", "status": "queued", "createdAt": "2026-06-06T00:00:00Z"},
            {"jobId": "2", "status": "queued", "createdAt": "2026-06-06T00:00:01Z"},
        ])
        summary = summarize_queue(jobs, policy=QueueBackpressurePolicy(enabled=True, max_ready_jobs=1, max_oldest_ready_age_seconds=999999999))
        self.assertEqual(summary["readyCount"], 2)
        self.assertTrue(summary["backpressure"]["active"])
        self.assertIn("ready_depth_exceeded", summary["backpressure"]["reasons"])

    def test_reconciliation_reports_safe_replay_cases(self) -> None:
        jobs = FakeJobs([
            {"jobId": "done_bad", "status": "done", "result": {}},
            {"jobId": "uploaded", "status": "uploading_result", "result": {"publicResultUrl": "https://cdn/result.png"}, "processing": {}},
            {"jobId": "failed", "status": "failed", "error": {}},
        ])
        report = reconcile_jobs(jobs, limit=10)
        types = {finding["type"] for finding in report["findings"]}
        self.assertIn("done_missing_public_url", types)
        self.assertIn("uploaded_missing_camera_callback", types)
        self.assertIn("failed_missing_category", types)


if __name__ == "__main__":
    unittest.main()
