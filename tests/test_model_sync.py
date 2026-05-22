from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.model_sync import plan_sync, resolve_profile


class ModelSyncTests(unittest.TestCase):
    def test_core_profile_contains_try_on_assets(self) -> None:
        asset_keys = resolve_profile("core")
        self.assertIn("catvton_densepose", asset_keys)
        self.assertIn("sd15_inpainting", asset_keys)

    def test_optional_profile_is_empty_for_try_on_only_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = plan_sync(Path(tmpdir), "optional")
            self.assertEqual(plan["assets"], [])


if __name__ == "__main__":
    unittest.main()
