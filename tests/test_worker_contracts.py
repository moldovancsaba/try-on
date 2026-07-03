from __future__ import annotations

import unittest

from services.worker_contracts import (
    PROCESSING_PROFILE_GENERIC,
    PROCESSING_PROFILE_MOTOGP,
    PROCESSING_PROFILE_GOOGLE_EDGE_TRYON,
    normalize_job_document,
    normalize_processing_profile,
    validate_job_document,
    validate_suit_document,
)


class WorkerContractTests(unittest.TestCase):
    def test_normalize_processing_profile_supports_motogp_aliases(self) -> None:
        self.assertEqual(normalize_processing_profile("motogp"), PROCESSING_PROFILE_MOTOGP)
        self.assertEqual(normalize_processing_profile("motogp_leather_magic"), PROCESSING_PROFILE_MOTOGP)
        self.assertEqual(normalize_processing_profile("motogp-leather-magic"), PROCESSING_PROFILE_MOTOGP)
        self.assertEqual(normalize_processing_profile("unknown"), PROCESSING_PROFILE_GENERIC)

    def test_normalize_processing_profile_supports_google_edge_aliases(self) -> None:
        self.assertEqual(normalize_processing_profile("google_edge_tryon"), PROCESSING_PROFILE_GOOGLE_EDGE_TRYON)
        self.assertEqual(normalize_processing_profile("google-edge-tryon"), PROCESSING_PROFILE_GOOGLE_EDGE_TRYON)
        self.assertEqual(normalize_processing_profile("google_edge"), PROCESSING_PROFILE_GOOGLE_EDGE_TRYON)
        self.assertEqual(normalize_processing_profile("google-edge"), PROCESSING_PROFILE_GOOGLE_EDGE_TRYON)

    def test_validate_job_document_accepts_current_shape(self) -> None:
        errors = validate_job_document(
            {
                "schemaVersion": 1,
                "jobId": "job_1",
                "status": "queued",
                "stage": "queued",
                "source": {"submissionId": "sub_1", "imageUrl": "https://example.com/image.png"},
                "request": {"leatherSuitId": "suit_1", "processingProfile": PROCESSING_PROFILE_MOTOGP},
                "processing": {"attemptCount": 0},
            }
        )
        self.assertEqual(errors, [])

    def test_validate_job_document_accepts_setup_reference(self) -> None:
        errors = validate_job_document(
            {
                "schemaVersion": 1,
                "jobId": "job_2",
                "status": "queued",
                "stage": "queued",
                "source": {"submissionId": "sub_1", "imageUrl": "https://example.com/image.png", "cameraId": "camera_a"},
                "request": {
                    "leatherSuitId": "suit_1",
                    "setupId": "setup_motogp_main",
                    "processingProfile": PROCESSING_PROFILE_MOTOGP,
                },
            }
        )
        self.assertEqual(errors, [])

    def test_validate_job_document_flags_missing_fields(self) -> None:
        errors = validate_job_document({"status": "queued", "source": {}, "request": {}})
        self.assertIn("missing_job_id", errors)
        self.assertIn("missing_source_image_url", errors)
        self.assertIn("missing_submission_id", errors)
        self.assertIn("missing_leather_suit_id", errors)

    def test_normalize_job_document_maps_legacy_top_level_fields(self) -> None:
        job = normalize_job_document(
            {
                "jobId": "job_legacy",
                "submissionId": "sub_legacy",
                "sourceImageUrl": "https://example.com/source.png",
                "leatherSuitId": "suit_legacy",
                "cameraId": "camera_legacy",
            }
        )

        self.assertEqual(job["schemaVersion"], 1)
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["stage"], "queued")
        self.assertEqual(job["source"]["submissionId"], "sub_legacy")
        self.assertEqual(job["source"]["imageUrl"], "https://example.com/source.png")
        self.assertEqual(job["source"]["cameraId"], "camera_legacy")
        self.assertEqual(job["request"]["cameraId"], "camera_legacy")
        self.assertEqual(job["request"]["leatherSuitId"], "suit_legacy")
        self.assertEqual(job["processing"]["attemptCount"], 0)
        self.assertEqual(validate_job_document(job), [])

    def test_validate_job_document_accepts_google_edge_tryon(self) -> None:
        errors = validate_job_document(
            {
                "schemaVersion": 1,
                "jobId": "job_google_edge",
                "status": "queued",
                "stage": "queued",
                "source": {"submissionId": "sub_1", "imageUrl": "https://example.com/image.png"},
                "request": {
                    "leatherSuitId": "suit_1",
                    "processingProfile": PROCESSING_PROFILE_GOOGLE_EDGE_TRYON,
                },
                "processing": {"attemptCount": 0},
            }
        )
        self.assertEqual(errors, [])

    def test_validate_suit_document_requires_asset_reference(self) -> None:
        errors = validate_suit_document({"schemaVersion": 1, "leatherSuitId": "suit_1", "active": True})
        self.assertIn("missing_suit_asset_reference", errors)


if __name__ == "__main__":
    unittest.main()
