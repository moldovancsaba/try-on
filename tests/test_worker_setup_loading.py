from __future__ import annotations

import unittest
from typing import Any

from scripts.tryon_queue_worker import TryOnQueueWorker


class FakeSetups:
    def __init__(self, documents: dict[str, dict[str, Any]]) -> None:
        self._documents = documents

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        setup_id = query.get("setupId")
        document = self._documents.get(setup_id)
        if document is None:
            return None
        if query.get("active") is True and not document.get("active", True):
            return None
        return document


class WorkerSetupLoadingTests(unittest.TestCase):
    def make_worker(self, *, local_setups: dict[str, Any], mongo_setups: dict[str, Any]) -> TryOnQueueWorker:
        worker = TryOnQueueWorker.__new__(TryOnQueueWorker)
        worker.local_setups = local_setups
        worker.setups = FakeSetups(mongo_setups)
        return worker

    def test_mongo_only_setup_carries_real_config(self) -> None:
        worker = self.make_worker(
            local_setups={},
            mongo_setups={
                "remote_only_setup": {
                    "setupId": "remote_only_setup",
                    "active": True,
                    "isDefault": False,
                    "cameraId": None,
                    "rank": 10,
                    "revision": "v1",
                    "config": {"processing_profile": "google_edge_tryon", "steps": 40},
                }
            },
        )
        result = worker._load_setup_by_id("remote_only_setup")
        assert result is not None
        self.assertNotEqual(result["config"], {})
        self.assertEqual(result["config"]["processing_profile"], "google_edge_tryon")

    def test_mongo_setup_with_missing_config_returns_empty_dict(self) -> None:
        worker = self.make_worker(
            local_setups={},
            mongo_setups={
                "no_config_setup": {
                    "setupId": "no_config_setup",
                    "active": True,
                    "isDefault": False,
                    "cameraId": None,
                    "rank": 1,
                    "revision": "v1",
                }
            },
        )
        result = worker._load_setup_by_id("no_config_setup")
        assert result is not None
        self.assertEqual(result["config"], {})

    def test_mongo_setup_with_non_dict_config_returns_empty_dict(self) -> None:
        worker = self.make_worker(
            local_setups={},
            mongo_setups={
                "bad_config_setup": {
                    "setupId": "bad_config_setup",
                    "active": True,
                    "isDefault": False,
                    "cameraId": None,
                    "rank": 1,
                    "revision": "v1",
                    "config": None,
                }
            },
        )
        result = worker._load_setup_by_id("bad_config_setup")
        assert result is not None
        self.assertEqual(result["config"], {})

    def test_local_setup_wins_over_mongo_when_both_exist(self) -> None:
        worker = self.make_worker(
            local_setups={
                "shared_setup": {
                    "setupId": "shared_setup",
                    "active": True,
                    "config": {"processing_profile": "motogp_leather_magic"},
                }
            },
            mongo_setups={
                "shared_setup": {
                    "setupId": "shared_setup",
                    "active": True,
                    "isDefault": False,
                    "cameraId": None,
                    "rank": 1,
                    "revision": "v1",
                    "config": {"processing_profile": "google_edge_tryon"},
                }
            },
        )
        result = worker._load_setup_by_id("shared_setup")
        assert result is not None
        self.assertEqual(result["config"]["processing_profile"], "motogp_leather_magic")

    def test_setup_missing_from_both_sources_returns_none(self) -> None:
        worker = self.make_worker(local_setups={}, mongo_setups={})
        self.assertIsNone(worker._load_setup_by_id("nonexistent_setup"))


if __name__ == "__main__":
    unittest.main()
