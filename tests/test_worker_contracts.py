from __future__ import annotations

import unittest

from services.worker_contracts import (
    PROCESSING_PROFILE_GENERIC,
    PROCESSING_PROFILE_MOTOGP,
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

    def test_validate_job_document_accepts_current_shape(self) -> None:
        errors = validate_job_document(
            {
                "jobId": "job_1",
                "status": "queued",
                "source": {"submissionId": "sub_1", "imageUrl": "https://example.com/image.png"},
                "request": {"leatherSuitId": "suit_1", "processingProfile": PROCESSING_PROFILE_MOTOGP},
                "processing": {"attemptCount": 0},
            }
        )
        self.assertEqual(errors, [])

    def test_validate_job_document_flags_missing_fields(self) -> None:
        errors = validate_job_document({"status": "queued", "source": {}, "request": {}})
        self.assertIn("missing_job_id", errors)
        self.assertIn("missing_source_image_url", errors)
        self.assertIn("missing_submission_id", errors)
        self.assertIn("missing_leather_suit_id", errors)

    def test_validate_suit_document_requires_asset_reference(self) -> None:
        errors = validate_suit_document({"leatherSuitId": "suit_1", "active": True})
        self.assertIn("missing_suit_asset_reference", errors)


if __name__ == "__main__":
    unittest.main()
