from __future__ import annotations

import unittest

import numpy as np
from PIL import Image

from warp_repair import texture_repair_decision


class TextureRepairTests(unittest.TestCase):
    def test_disabled_texture_repair_reports_skip_reason(self) -> None:
        image = Image.fromarray(np.zeros((64, 64, 3), dtype=np.uint8))
        decision = texture_repair_decision(image, warp_strength=0.0)
        self.assertEqual(decision["action"], "skip")
        self.assertEqual(decision["reason"], "disabled")

    def test_high_complexity_texture_reports_bailout(self) -> None:
        rng = np.random.default_rng(42)
        noisy = Image.fromarray(rng.integers(0, 255, size=(128, 128, 3), dtype=np.uint8))
        decision = texture_repair_decision(noisy, warp_strength=1.0)
        self.assertEqual(decision["action"], "skip")
        self.assertIn(decision["reason"], {"high_complexity", "fine_detail"})


if __name__ == "__main__":
    unittest.main()
