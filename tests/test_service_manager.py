from __future__ import annotations

import unittest

from services import service_manager


class ServiceManagerTests(unittest.TestCase):
    def test_parse_etime_seconds_supports_minutes_and_days(self) -> None:
        self.assertEqual(service_manager._parse_etime_seconds("05:30"), 330)
        self.assertEqual(service_manager._parse_etime_seconds("01:05:30"), 3930)
        self.assertEqual(service_manager._parse_etime_seconds("2-01:00:00"), 176400)

    def test_parse_etime_seconds_rejects_invalid_values(self) -> None:
        self.assertIsNone(service_manager._parse_etime_seconds(""))
        self.assertIsNone(service_manager._parse_etime_seconds("abc"))

    def test_invalid_service_action_raises(self) -> None:
        with self.assertRaises(ValueError):
            service_manager.perform_service_action("missing", "restart")


if __name__ == "__main__":
    unittest.main()
