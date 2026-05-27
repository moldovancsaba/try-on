from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.worker_settings import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    get_worker_settings_path,
    load_worker_settings,
    normalize_worker_settings,
    save_worker_settings,
)


class WorkerSettingsTests(unittest.TestCase):
    def test_normalize_worker_settings_clamps_invalid_interval(self) -> None:
        settings = normalize_worker_settings({"enabled": "yes", "pollIntervalSeconds": 999})
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["pollIntervalSeconds"], DEFAULT_POLL_INTERVAL_SECONDS)

    def test_save_and_load_worker_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            save_worker_settings({"enabled": False, "pollIntervalSeconds": 180}, app_root=root)
            loaded = load_worker_settings(app_root=root)
            self.assertFalse(loaded["enabled"])
            self.assertEqual(loaded["pollIntervalSeconds"], 180)
            self.assertTrue(get_worker_settings_path(root).exists())


if __name__ == "__main__":
    unittest.main()
