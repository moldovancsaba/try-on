from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from services.quality_contracts import validate_image_output


class QualityContractTests(unittest.TestCase):
    def test_black_image_fails_try_on_contract(self) -> None:
        image = Image.fromarray(np.zeros((1024, 768, 3), dtype=np.uint8))
        result = validate_image_output("try_on", image)
        self.assertFalse(result["passed"])
        self.assertIn("Output is near-black.", result["failures"])

    def test_reasonable_image_passes_try_on_contract(self) -> None:
        gradient = np.tile(np.linspace(0, 255, 768, dtype=np.uint8), (1024, 1))
        image = Image.fromarray(np.stack([gradient, gradient, gradient], axis=2))
        mask = Image.fromarray(np.pad(np.ones((400, 250), dtype=np.uint8) * 255, ((200, 424), (200, 318))))
        result = validate_image_output("try_on", image, mask=mask)
        self.assertTrue(result["passed"])

if __name__ == "__main__":
    unittest.main()
