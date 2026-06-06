from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.tryon_queue_worker import TryOnQueueWorker


class FakeJobs:
    def __init__(self, job: dict):
        self.job = job
        self.updates: list[dict] = []

    def find_one(self, query: dict) -> dict:
        return self.job

    def update_one(self, query: dict, update: dict) -> None:
        self.updates.append(update)


class WorkerPublicationTests(unittest.TestCase):
    def make_worker(self, job: dict) -> TryOnQueueWorker:
        worker = TryOnQueueWorker.__new__(TryOnQueueWorker)
        worker.jobs = FakeJobs(job)
        worker.config = SimpleNamespace(imgbb_api_key="test", worker_id="worker_test")
        worker.emit_event = Mock()
        return worker

    def test_existing_public_url_is_reused_without_upload(self) -> None:
        worker = self.make_worker(
            {
                "jobId": "job_1",
                "result": {
                    "publicResultUrl": "https://cdn.example/result.png",
                    "deleteUrl": "https://cdn.example/delete",
                },
                "processing": {},
            }
        )
        worker.upload_to_imgbb = Mock(side_effect=AssertionError("upload should not run"))

        with tempfile.TemporaryDirectory() as tmp:
            upload = worker.ensure_published_result("job_1", Path(tmp) / "result.png", job_snapshot=worker.jobs.job)

        self.assertEqual(upload["imageUrl"], "https://cdn.example/result.png")
        self.assertEqual(upload["deleteUrl"], "https://cdn.example/delete")
        worker.upload_to_imgbb.assert_not_called()

    def test_publication_result_stores_delete_url_and_legacy_alias(self) -> None:
        worker = self.make_worker({"jobId": "job_2", "processing": {}})

        worker._upsert_publication_result(
            "job_2",
            {"imageUrl": "https://cdn.example/result.png", "deleteUrl": "https://cdn.example/delete"},
            "2026-06-06T00:00:00Z",
        )

        update = worker.jobs.updates[-1]["$set"]
        self.assertEqual(update["result"]["publicResultUrl"], "https://cdn.example/result.png")
        self.assertEqual(update["result"]["deleteUrl"], "https://cdn.example/delete")
        self.assertEqual(update["result"]["imgbbDeleteUrl"], "https://cdn.example/delete")
        self.assertEqual(update["processing.publicationState"], "uploaded")

    def test_camera_completion_is_skipped_when_already_notified(self) -> None:
        worker = self.make_worker(
            {
                "jobId": "job_3",
                "source": {"submissionId": "sub_3"},
                "processing": {"cameraNotifiedAt": "2026-06-06T00:00:00Z"},
            }
        )
        worker.notify_camera_completion = Mock(side_effect=AssertionError("callback should not run"))

        notified = worker.ensure_camera_notified("job_3", {"imageUrl": "https://cdn.example/result.png"})

        self.assertFalse(notified)
        worker.notify_camera_completion.assert_not_called()
        worker.emit_event.assert_called()

    def test_successful_camera_completion_marks_notified_state(self) -> None:
        worker = self.make_worker(
            {
                "jobId": "job_4",
                "source": {"submissionId": "sub_4"},
                "processing": {
                    "resolvedSetupId": "setup_1",
                    "resolvedSetupSource": "job.assigned",
                    "resolvedSetupProfile": "local_profile",
                    "resolvedSetupRevision": "rev_1",
                },
            }
        )
        worker.notify_camera_completion = Mock()

        notified = worker.ensure_camera_notified("job_4", {"imageUrl": "https://cdn.example/result.png"})

        self.assertTrue(notified)
        worker.notify_camera_completion.assert_called_once()
        update = worker.jobs.updates[-1]["$set"]
        self.assertIn("processing.cameraNotifiedAt", update)
        self.assertEqual(update["processing.publicationState"], "camera_notified")

    def test_second_timeout_is_final_failed_not_retry_wait(self) -> None:
        worker = self.make_worker({"jobId": "job_timeout", "processing": {"attemptCount": 2}})
        worker.config.max_attempts = 3

        outcome = worker.schedule_retry_or_failure(
            worker.jobs.job,
            "transient_runtime_error",
            "HTTPSConnectionPool read timed out. (read timeout=300)",
        )

        self.assertEqual(outcome, "failed")
        update = worker.jobs.updates[-1]["$set"]
        self.assertEqual(update["status"], "failed")
        self.assertEqual(update["error"]["code"], "timeout_retry_limit_reached")

    def test_first_timeout_can_retry(self) -> None:
        worker = self.make_worker({"jobId": "job_timeout_retry", "processing": {"attemptCount": 1}})
        worker.config.max_attempts = 3

        outcome = worker.schedule_retry_or_failure(
            worker.jobs.job,
            "transient_runtime_error",
            "HTTPSConnectionPool read timed out. (read timeout=300)",
        )

        self.assertEqual(outcome, "retry_wait")
        update = worker.jobs.updates[-1]["$set"]
        self.assertEqual(update["status"], "retry_wait")


if __name__ == "__main__":
    unittest.main()
