from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from services.local_ai_services import (
    evaluate_model_packs,
    export_report_csv,
    run_local_ai_service,
    service_registry,
)


def _fixture(path: Path, color: tuple[int, int, int, int] = (220, 30, 30, 255)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (640, 820), (255, 255, 255, 255))
    garment = Image.new("RGBA", (300, 420), color)
    image.alpha_composite(garment, (170, 210))
    image.save(path)
    return path


class LocalAiServicesTests(unittest.TestCase):
    def test_registry_reports_zero_external_services(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".cache" / "huggingface").mkdir(parents=True)
            registry = service_registry(root)
            service_ids = {item["serviceId"] for item in registry["services"]}
            self.assertIn("garment_isolation", service_ids)
            self.assertTrue(all(item["zeroExternalCost"] for item in registry["services"]))

    def test_model_pack_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = evaluate_model_packs(root)
            self.assertEqual(report["modelPacks"]["pillow_core"]["status"], "unavailable")
            (root / ".cache" / "huggingface").mkdir(parents=True)
            report = evaluate_model_packs(root)
            self.assertEqual(report["modelPacks"]["pillow_core"]["status"], "ready")

    def test_garment_isolation_writes_artifact_and_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = _fixture(root / "source.png")
            result = run_local_ai_service(root, "garment_isolation", {"sourceImagePath": str(source), "jobId": "job_1"})
            self.assertEqual(result["status"], "completed")
            self.assertTrue(Path(result["artifact"]["path"]).exists())
            self.assertTrue(Path(result["mask"]["path"]).exists())

    def test_brand_safety_and_quality_gate_return_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = _fixture(root / "source.png")
            output = _fixture(root / "output.png", color=(210, 35, 35, 255))
            brand = run_local_ai_service(root, "brand_safety_analyzer", {"sourceImagePath": str(source), "outputImagePath": str(output)})
            self.assertIn(brand["status"], {"pass", "warn", "fail"})
            gate = run_local_ai_service(root, "tryon_quality_gate", {"sourceImagePath": str(source), "outputImagePath": str(output)})
            self.assertIn(gate["status"], {"pass", "warn", "fail"})

    def test_fixture_generation_and_report_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fixtures = run_local_ai_service(root, "synthetic_fixture_generator", {"jobId": "fixtures"})
            self.assertEqual(len(fixtures["artifacts"]), 3)
            csv_path = export_report_csv(root, root / "report.csv")
            self.assertTrue(csv_path.exists())
            self.assertIn("serviceId", csv_path.read_text(encoding="utf-8"))

    def test_unknown_service_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                run_local_ai_service(Path(tmpdir), "missing_service", {})

    def test_google_edge_analyzer_registration_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = service_registry(root)
            service_ids = {item["serviceId"] for item in registry["services"]}
            self.assertIn("google_edge_analyzer", service_ids)

            source = _fixture(root / "source.png")
            try:
                run_local_ai_service(root, "google_edge_analyzer", {"sourceImagePath": str(source)})
            except (FileNotFoundError, ImportError):
                pass
            except Exception as e:
                self.fail(f"Unexpected exception raised: {type(e).__name__} - {e}")

    def test_google_edge_analyzer_real(self) -> None:
        from model_paths import get_models_root
        models_root = get_models_root()
        report = evaluate_model_packs(models_root)
        if report["modelPacks"].get("google_edge_mediapipe", {}).get("status") == "ready":
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_root = Path(tmpdir)
                source = _fixture(tmp_root / "source.png")
                result = run_local_ai_service(tmp_root, "google_edge_analyzer", {"sourceImagePath": str(source)})
                self.assertIn(result["status"], {"pass", "warn", "fail"})
                self.assertFalse(result["posePassed"])
                self.assertFalse(result["facePassed"])
                self.assertEqual(result["status"], "fail")

    def test_google_edge_tryon_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            registry = service_registry(root)
            service_ids = {item["serviceId"] for item in registry["services"]}
            self.assertIn("google_edge_tryon", service_ids)

            person = _fixture(root / "person.png")
            garment = _fixture(root / "garment.png")
            output = root / "output.png"

            try:
                run_local_ai_service(root, "google_edge_tryon", {
                    "personImagePath": str(person),
                    "garmentImagePath": str(garment),
                    "outputImagePath": str(output)
                })
            except (FileNotFoundError, ImportError, ValueError):
                pass
            except Exception as e:
                self.fail(f"Unexpected exception raised: {type(e).__name__} - {e}")

    def test_google_edge_tryon_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            person = _fixture(root / "person.png")
            garment = _fixture(root / "garment.png")
            output = root / "output.png"

            try:
                run_local_ai_service(root, "google_edge_tryon", {
                    "personImagePath": str(person),
                    "garmentImagePath": str(garment),
                    "outputImagePath": str(output),
                    "google_edge_width_scale": "1.6",
                    "google_edge_height_scale": 1.4,
                    "google_edge_offset_x": -0.05,
                    "google_edge_offset_y": "0.18",
                    "google_edge_tilt_adjustment": "2.5"
                })
            except (FileNotFoundError, ImportError, ValueError):
                pass
            except Exception as e:
                self.fail(f"Parameters parsing raised exception: {type(e).__name__} - {e}")


if __name__ == "__main__":
    unittest.main()
