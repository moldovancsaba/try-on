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


if __name__ == "__main__":
    unittest.main()
