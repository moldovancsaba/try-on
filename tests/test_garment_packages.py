from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.garment_packages import PACKAGE_SCHEMA_VERSION, load_garment_package, safe_package_name


class GarmentPackageTests(unittest.TestCase):
    def test_load_package_preserves_keypoints_and_garment_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            package_dir = Path(tmpdir) / "jersey"
            package_dir.mkdir()
            (package_dir / "garment.png").write_bytes(b"png")
            (package_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": PACKAGE_SCHEMA_VERSION,
                        "name": "jersey",
                        "keypoints": [{"name": "left_shoulder", "x": 0.25, "y": 0.3}],
                    }
                ),
                encoding="utf-8",
            )
            (package_dir / "package.json").write_text(
                json.dumps({"schemaVersion": PACKAGE_SCHEMA_VERSION, "garment_file": "garment.png"}),
                encoding="utf-8",
            )

            package = load_garment_package(Path(tmpdir), "jersey")

            self.assertEqual(package.name, "jersey")
            self.assertEqual(package.garment_path, (package_dir / "garment.png").resolve())
            self.assertEqual(package.metadata["keypoints"][0]["name"], "left_shoulder")

    def test_safe_package_name_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            safe_package_name("../bad")


if __name__ == "__main__":
    unittest.main()
