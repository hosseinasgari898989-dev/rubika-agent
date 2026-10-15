from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain.inspector import InspectionResult
from agent.brain.models import Candidate
from agent.brain.vision_moderator import CloudflareVideoModerator, VisionModeratorError


class FakeCloud:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "has_music": True,
            "music_probability": 0.97,
            "music_decision_reason": "music_detected_above_threshold",
            "music_detection_method": "cloudflare_clef_omni_audio",
            "music_model": "@cf/cloudflare/clef-omni",
            "music_confidence": 0.94,
            "human_review_required": True,
            "auto_approved": False,
            "publishing": False,
        }
        self.error = error
        self.calls = []

    def moderate_music(self, audio_data_url, *, platform):
        self.calls.append({"audio_data_url": audio_data_url, "platform": platform})
        if self.error:
            raise self.error
        return dict(self.result)


class CloudflareMusicModeratorTests(unittest.TestCase):
    def candidate(self):
        return Candidate(
            platform="tiktok",
            canonical_url="https://www.tiktok.com/@creator/video/7691234567890123456",
            external_id="7691234567890123456",
            title="Poppy Playtime fan edit",
            caption="#PoppyPlaytime #edit",
            source_key="meloaep_",
            media_type="video",
        )

    def inspection(self, _path, _kind=None, *, duration=10.0, has_audio=True):
        return InspectionResult(
            ok=True, media_type="video", sha256="a" * 64,
            size_bytes=1_000_000, mime_type="video/mp4",
            width=1920, height=1080, duration_seconds=duration,
            has_audio=has_audio, video_codec="h264",
            audio_codec="aac" if has_audio else None,
        )

    @staticmethod
    def fake_ffmpeg(command, **kwargs):
        output = Path(command[-1])
        output.write_bytes(b"ID3" + b"x" * 70000)
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_extracts_and_sends_only_mp3_audio_to_ai(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder source file")
            cloud = FakeCloud()
            moderator = CloudflareVideoModerator(cloud, inspector=self.inspection)
            with patch("agent.brain.vision_moderator.subprocess.run", side_effect=self.fake_ffmpeg) as ffmpeg:
                result = moderator(self.candidate(), video)

        self.assertEqual(ffmpeg.call_count, 1)
        command = ffmpeg.call_args.args[0]
        self.assertIn("0:a:0", command)
        self.assertIn("-vn", command)
        self.assertIn("libmp3lame", command)
        self.assertNotIn("0:v:0", command)
        self.assertTrue(cloud.calls[0]["audio_data_url"].startswith("data:audio/mpeg;base64,"))
        self.assertEqual(cloud.calls[0]["platform"], "tiktok")
        self.assertIs(result["has_music"], True)
        self.assertEqual(result["music_probability"], 0.97)
        self.assertNotIn("is_poppy_playtime", result)
        self.assertNotIn("is_edit", result)
        self.assertNotIn("high_quality_edit", result)
        self.assertTrue(result["human_review_required"])
        self.assertFalse(result["auto_approved"])
        self.assertFalse(result["publishing"])

    def test_ambiguous_music_answer_is_preserved_for_diagnostics(self):
        result_payload = {
            "music_probability": 0.55,
            "music_decision_reason": "music_probability_ambiguous",
            "music_detection_method": "cloudflare_clef_omni_audio",
            "music_confidence": 0.1,
        }
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder source file")
            moderator = CloudflareVideoModerator(
                FakeCloud(result=result_payload), inspector=self.inspection
            )
            with patch("agent.brain.vision_moderator.subprocess.run", side_effect=self.fake_ffmpeg):
                result = moderator(self.candidate(), video)
        self.assertNotIn("has_music", result)
        self.assertEqual(result["music_probability"], 0.55)
        self.assertEqual(result["music_decision_reason"], "music_probability_ambiguous")
        self.assertTrue(result["music_check_uncertain"])

    def test_no_audio_stream_does_not_call_ai(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder source file")
            cloud = FakeCloud()
            moderator = CloudflareVideoModerator(
                cloud, inspector=lambda path, kind=None: self.inspection(path, kind, has_audio=False)
            )
            result = moderator(self.candidate(), video)
        self.assertIs(result["has_music"], False)
        self.assertEqual(result["music_decision_reason"], "source_has_no_audio_stream")
        self.assertEqual(cloud.calls, [])

    def test_missing_ffmpeg_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder source file")
            moderator = CloudflareVideoModerator(FakeCloud(), inspector=self.inspection)
            with patch(
                "agent.brain.vision_moderator.subprocess.run",
                side_effect=FileNotFoundError(),
            ):
                with self.assertRaises(VisionModeratorError) as caught:
                    moderator(self.candidate(), video)
        self.assertEqual(caught.exception.code, "ffmpeg_not_installed")

    def test_model_errors_are_sanitized(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder source file")
            moderator = CloudflareVideoModerator(
                FakeCloud(error=RuntimeError("private provider response")), inspector=self.inspection
            )
            with patch("agent.brain.vision_moderator.subprocess.run", side_effect=self.fake_ffmpeg):
                with self.assertRaises(VisionModeratorError) as caught:
                    moderator(self.candidate(), video)
        self.assertNotIn("private provider response", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
