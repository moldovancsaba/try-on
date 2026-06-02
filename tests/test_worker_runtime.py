from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.single_task_lock import SingleTaskLock
from services.worker_runtime import append_worker_event, load_worker_status, read_recent_worker_events, write_worker_status


class WorkerRuntimeTests(unittest.TestCase):
    def test_status_defaults_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status = load_worker_status(app_root=Path(tmpdir))
            self.assertFalse(status["workerRunning"])

    def test_events_append_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            append_worker_event({"event": "worker_started"}, app_root=root)
            append_worker_event({"event": "claimed_job"}, app_root=root)
            events = read_recent_worker_events(limit=10, app_root=root)
            self.assertEqual([event["event"] for event in events], ["worker_started", "claimed_job"])

    def test_write_status_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            write_worker_status({"workerRunning": True, "currentJobId": "job_1"}, app_root=root)
            status = load_worker_status(app_root=root)
            self.assertTrue(status["workerRunning"])
            self.assertEqual(status["currentJobId"], "job_1")

    def test_single_task_lock_rejects_second_holder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first = SingleTaskLock("queue-worker-process", app_root=root)
            second = SingleTaskLock("queue-worker-process", app_root=root)
            self.assertTrue(first.acquire(blocking=False))
            try:
                self.assertFalse(second.acquire(blocking=False))
            finally:
                first.release()
            self.assertTrue(second.acquire(blocking=False))
            second.release()


if __name__ == "__main__":
    unittest.main()
