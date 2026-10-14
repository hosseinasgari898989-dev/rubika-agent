from __future__ import annotations

import base64
import io
import json
import unittest
from unittest.mock import patch

from agent.brain.cloud_state import CloudState, CloudStateError
from agent.brain.recovery import call_with_bounded_retry, is_retryable_error
from agent.brain.models import Candidate, Evaluation, InspectionResult
from types import SimpleNamespace


TOKEN = "test-token-value-with-more-than-thirty-two-characters"


class CloudStateTests(unittest.TestCase):
    def test_brain_retries_only_allowlisted_transient_infrastructure_errors(self):
        calls = []
        sleeps = []

        def operation():
            calls.append(True)
            if len(calls) == 1:
                raise CloudStateError("control_api_unreachable")
            return {"has_music": True}

        value, receipt = call_with_bounded_retry(
            operation, sleeper=lambda seconds: sleeps.append(seconds), sleep_seconds=0.25
        )
        self.assertEqual(value, {"has_music": True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [0.25])
        self.assertTrue(receipt["recovered"])
        self.assertEqual(receipt["error_codes"], ["control_api_unreachable"])

    def test_brain_never_retries_content_uncertainty_or_permission_errors(self):
        class UncertainError(RuntimeError):
            code = "music_probability_ambiguous"

        self.assertFalse(is_retryable_error(UncertainError()))
        calls = []
        with self.assertRaises(UncertainError):
            call_with_bounded_retry(
                lambda: (calls.append(True), (_ for _ in ()).throw(UncertainError()))[1],
                sleeper=lambda _seconds: self.fail("uncertain content must not be retried"),
            )
        self.assertEqual(len(calls), 1)

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

    def test_get_seen_index_reads_pages_and_normalizes_hashes(self):
        state = CloudState("https://worker.example", TOKEN)
        pages = iter([
            {
                "ok": True,
                "items": [
                    {"platform": "youtube", "external_id": "vkVFgmgLo1s", "media_sha256": "A" * 64},
                    {"platform": "tiktok", "external_id": "7616247520657771796", "media_sha256": None},
                ],
            },
            {
                "ok": True,
                "items": [
                    {"platform": "instagram", "external_id": "AbCdEf_1234", "media_sha256": "b" * 64},
                ],
            },
        ])
        urls = []

        def fake_open(request, timeout):
            urls.append(request.full_url)
            return io.BytesIO(json.dumps(next(pages)).encode("utf-8"))

        with patch("urllib.request.urlopen", side_effect=fake_open):
            index = state.get_seen_index(page_size=2)

        self.assertEqual(index["ids"], {
            "youtube:vkVFgmgLo1s",
            "tiktok:7616247520657771796",
            "instagram:AbCdEf_1234",
        })
        self.assertEqual(index["hashes"], {"a" * 64, "b" * 64})
        self.assertTrue(urls[0].endswith("/api/brain/items/seen?limit=2&offset=0"))
        self.assertTrue(urls[1].endswith("/api/brain/items/seen?limit=2&offset=2"))

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

    def test_http_failure_exposes_only_safe_api_error_code(self):
        state = CloudState("https://worker.example", TOKEN)
        error = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
            "https://worker.example/api/brain/items/upsert",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error":"invalid_canonical_url","secret":"do not expose this"}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(CloudStateError) as caught:
                state.upsert_item({
                    "platform": "mob_official",
                    "external_id": "x-2107878755079213476",
                    "canonical_url": "https://x.com/mobgamesstudios/status/2107878755079213476",
                    "source_key": "x.com/mobgamesstudios",
                    "status": "discovered",
                })

        self.assertEqual(caught.exception.code, "control_api_http_400_invalid_canonical_url")
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("do not expose this", str(caught.exception))

    def test_rejects_oversized_payload_before_network(self):
        state = CloudState("https://worker.example", TOKEN)
        with self.assertRaises(ValueError):
            state._request("POST", "/api/brain/events", {"details": {"large": "x" * 40000}})



    def test_translate_official_uses_authenticated_worker_and_validates_persian_result(self):
        state = CloudState("https://worker.example", TOKEN)
        captured = {}

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.method
            captured["auth"] = request.get_header("Authorization")
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return io.BytesIO(json.dumps({
                "ok": True,
                "translation": "ماب انترتینمنت خبر رسمی منتشر کرد.",
                "model": "@cf/meta/llama-3.1-8b-instruct-fp8",
                "publishing": False,
            }).encode("utf-8"))

        with patch("urllib.request.urlopen", side_effect=fake_open):
            result = state.translate_official("Mob Entertainment published a new official update.")

        self.assertEqual(result, "ماب انترتینمنت خبر رسمی منتشر کرد.")
        self.assertTrue(captured["url"].endswith("/api/brain/translate-official"))
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["auth"], "Bearer " + TOKEN)
        self.assertIn("text", captured["payload"])


    def test_translate_official_rejects_mixed_cyrillic_output(self):
        state = CloudState("https://worker.example", TOKEN)

        def fake_open(request, timeout):
            return io.BytesIO(json.dumps({
                "ok": True,
                "translation": "به عمق بازی بروید, еще اسرارآمیزتر.",
                "publishing": False,
            }).encode("utf-8"))

        with patch("urllib.request.urlopen", side_effect=fake_open):
            with self.assertRaises(CloudStateError) as caught:
                state.translate_official("Explore the depths of the game.")

        self.assertEqual(caught.exception.code, "control_api_translation_wrong_script")

    def test_translate_official_rejects_too_long_text_before_network(self):
        state = CloudState("https://worker.example", TOKEN)
        with patch("urllib.request.urlopen") as opener:
            with self.assertRaises(CloudStateError) as caught:
                state.translate_official("x" * 4501)
        self.assertEqual(caught.exception.code, "translation_text_too_long")
        opener.assert_not_called()

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
            checks={"has_music": True},
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
        self.assertEqual(captured["moderation"]["has_music"], True)
        self.assertNotIn("high_effort", captured["moderation"])
        self.assertEqual(captured["validation"]["ok"], True)



    def test_moderate_music_allows_bounded_audio_payload_over_normal_json_limit(self):
        state = CloudState("https://worker.example", TOKEN)
        captured = {}

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = request.data
            captured["auth"] = request.get_header("Authorization")
            return io.BytesIO(json.dumps({
                "ok": True,
                "model": "@cf/cloudflare/clef-omni",
                "checks": {"has_music": True},
                "probabilities": {"has_music": 0.96},
                "confidence": 0.92,
                "diagnostic": {"has_music": "music_detected_above_threshold"},
                "human_review_required": True,
                "auto_approved": False,
                "publishing": False,
            }).encode("utf-8"))

        binary = b"ID3" + b"x" * 40000
        audio = "data:audio/mpeg;base64," + base64.b64encode(binary).decode("ascii")
        with patch("urllib.request.urlopen", side_effect=fake_open):
            result = state.moderate_music(audio, platform="youtube")

        self.assertTrue(result["has_music"])
        self.assertNotIn("poppy_relevant", result)
        self.assertNotIn("high_effort", result)
        self.assertTrue(result["human_review_required"])
        self.assertFalse(result["auto_approved"])
        self.assertGreater(len(captured["body"]), 32 * 1024)
        self.assertTrue(captured["body"].find(b"data:audio/mpeg;base64,") >= 0)
        self.assertTrue(captured["url"].endswith("/api/brain/moderate-music"))
        self.assertEqual(captured["auth"], "Bearer " + TOKEN)

if __name__ == "__main__":
    unittest.main()
