from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from agent.brain.cloud_state import CloudState, CloudStateError
from agent.brain.models import Candidate, Evaluation, InspectionResult
from types import SimpleNamespace


TOKEN = "test-token-value-with-more-than-thirty-two-characters"


class CloudStateTests(unittest.TestCase):
    def test_requires_https_and_long_token(self):
        with self.assertRaises(ValueError):
            CloudState("http://worker.example", TOKEN)
        with self.assertRaises(ValueError):
            CloudState("https://worker.example", "short")

    def test_create_run_uses_auth_without_printing_it(self):
        state = CloudState("https://worker.example", TOKEN)
        captured = {}

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.method
            captured["auth"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return io.BytesIO(b'{"ok":true,"run_id":"run-test-0001","status":"running"}')

        with patch("urllib.request.urlopen", side_effect=fake_open):
            result = state.create_run("manual", "run-test-0001")

        self.assertEqual(result, "run-test-0001")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["auth"], "Bearer " + TOKEN)
        self.assertEqual(captured["payload"]["id"], "run-test-0001")

    def test_rejects_paths_outside_brain_namespace(self):
        state = CloudState("https://worker.example", TOKEN)
        with self.assertRaises(ValueError):
            state._request("POST", "/api/status", {})

    def test_item_attempt_path_and_safe_error_mapping(self):
        state = CloudState("https://worker.example", TOKEN)
        captured = {}

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            return io.BytesIO(b'{"ok":true,"item_id":"tiktok:123","attempt_number":1,"status":"sent"}')

        with patch("urllib.request.urlopen", side_effect=fake_open):
            result = state.record_attempt("tiktok:123", 1, "sent", message_id="m1")

        self.assertTrue(result["ok"])
        self.assertTrue(captured["url"].endswith("/api/brain/items/tiktok:123/attempts"))
        with self.assertRaises(ValueError):
            state.record_attempt("tiktok/123", 1, "sent")

    def test_http_failure_is_sanitized(self):
        state = CloudState("https://worker.example", TOKEN)
        error = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
            "https://worker.example/api/brain/runs",
            401,
            "unauthorized",
            {},
            io.BytesIO(b'{"secret":"do not include"}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(CloudStateError) as caught:
                state.create_run("manual", "run-test-0001")
        self.assertEqual(caught.exception.code, "control_api_http_401")
        self.assertNotIn("secret", str(caught.exception))

    def test_rejects_oversized_payload_before_network(self):
        state = CloudState("https://worker.example", TOKEN)
        with self.assertRaises(ValueError):
            state._request("POST", "/api/brain/events", {"details": {"large": "x" * 40000}})


    def test_upsert_staged_preserves_canonical_youtube_id_and_never_approves(self):
        state = CloudState("https://worker.example", TOKEN)
        candidate = Candidate(
            platform="youtube",
            canonical_url="https://www.youtube.com/watch?v=vkVFgmgLo1s",
            external_id="vkVFgmgLo1s",
            title="Poppy Playtime edit",
            caption="#PoppyPlaytime #edit",
            source_key="@creator",
            media_type="video",
            source_tier="trusted",
        )
        inspection = InspectionResult(
            ok=True, media_type="video", sha256="a" * 64, size_bytes=1000000,
            mime_type="video/mp4", width=1080, height=1080,
            duration_seconds=12.0, has_audio=True, video_codec="h264", audio_codec="aac",
        )
        evaluation = Evaluation(
            decision="eligible", score=100, requires_review=False,
            checks={
                "poppy_relevant": True, "is_edit": True, "insults_poppy": False,
                "insults_other_games": False, "high_effort": True, "has_music": True,
            },
        )
        staged = SimpleNamespace(
            candidate=candidate, inspection=inspection, evaluation=evaluation,
            storage_key="/Rubika-Agent/staging/youtube-vkVFgmgLo1s.mp4",
            caption=candidate.caption,
        )
        captured = {}
        state.upsert_item = lambda payload: (captured.update(payload) or {"ok": True})
        result = state.upsert_staged(staged)
        self.assertTrue(result["ok"])
        self.assertEqual(captured["canonical_url"], candidate.canonical_url)
        self.assertEqual(captured["review_state"], "pending")
        self.assertEqual(captured["status"], "ready")
        self.assertEqual(captured["media_sha256"], "a" * 64)
        self.assertEqual(captured["moderation"]["high_effort"], True)
        self.assertEqual(captured["validation"]["ok"], True)


if __name__ == "__main__":
    unittest.main()
