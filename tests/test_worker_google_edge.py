from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.tryon_queue_worker import TryOnQueueWorker


class WorkerGoogleEdgeTests(unittest.TestCase):
    def make_worker(self) -> TryOnQueueWorker:
        worker = TryOnQueueWorker.__new__(TryOnQueueWorker)
        worker.config = SimpleNamespace(
            local_tryon_api_url="http://127.0.0.1:7860/api/tryon/run",
            local_tryon_timeout_seconds=30
        )
        worker._call_provider = lambda name, call_fn: call_fn()
        return worker

    @patch("requests.get")
    def test_local_google_edge_api_is_ready_success(self, mock_get) -> None:
        worker = self.make_worker()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "assets": {
                "google_edge_mediapipe": {
                    "ready": True
                }
            }
        }
        mock_get.return_value = mock_response

        self.assertTrue(worker.local_google_edge_api_is_ready())
        mock_get.assert_called_once_with("http://127.0.0.1:7860/api/capabilities", timeout=10)

    @patch("requests.get")
    def test_local_google_edge_api_is_ready_failure(self, mock_get) -> None:
        worker = self.make_worker()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "assets": {
                "google_edge_mediapipe": {
                    "ready": False
                }
            }
        }
        mock_get.return_value = mock_response

        self.assertFalse(worker.local_google_edge_api_is_ready())

    @patch("requests.post")
    def test_call_google_edge_tryon_api_success(self, mock_post) -> None:
        worker = self.make_worker()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "completed"}
        mock_post.return_value = mock_response

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            person_path = tmp_root / "person.png"
            suit_path = tmp_root / "suit.png"
            output_path = tmp_root / "output.png"
            
            # create dummy output file since the method asserts it exists
            output_path.write_bytes(b"dummy")

            payload = {"seed": 42}
            result = worker.call_google_edge_tryon_api(person_path, suit_path, output_path, payload)

            self.assertEqual(result, {"status": "completed"})
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            self.assertEqual(args[0], "http://127.0.0.1:7860/api/local-ai/google-edge/tryon")
            self.assertEqual(kwargs["json"]["personImagePath"], str(person_path))
            self.assertEqual(kwargs["json"]["garmentImagePath"], str(suit_path))
            self.assertEqual(kwargs["json"]["outputImagePath"], str(output_path))
            self.assertEqual(kwargs["json"]["seed"], 42)
