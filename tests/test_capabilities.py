from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.capabilities import STATUS_READY, build_capability_report


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


class CapabilityReportTests(unittest.TestCase):
    def test_try_on_ready_with_core_assets_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _touch(root / ".cache" / "huggingface" / ".keep")
            _touch(root / "processors/catvton-segmentation/DensePose/model_final_162be9.pkl")
            _touch(root / "processors/catvton-segmentation/DensePose/densepose_rcnn_R_50_FPN_s1x.yaml")
            _touch(root / "processors/catvton-segmentation/SCHP/exp-schp-201908301523-atr.pth")
            _touch(root / "processors/catvton-segmentation/SCHP/exp-schp-201908261155-lip.pth")
            _touch(root / "checkpoints/sd15-inpainting/model_index.json")
            _touch(root / "checkpoints/sd15-inpainting/unet/config.json")
            _touch(root / "checkpoints/sd15-inpainting/vae/config.json")
            _touch(root / "vae/sd15-vae-ft-mse/config.json")

            report = build_capability_report(root)
            self.assertEqual(report["features"]["try_on"]["status"], STATUS_READY)

    def test_report_only_exposes_try_on_feature(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _touch(root / ".cache" / "huggingface" / ".keep")
            report = build_capability_report(root)
            self.assertEqual(tuple(report["features"].keys()), ("try_on",))


if __name__ == "__main__":
    unittest.main()
