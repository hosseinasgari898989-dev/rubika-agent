from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from agent.brain.inspector import InspectionResult
from agent.brain.models import Candidate
from agent.brain.vision_moderator import CloudflareVisionModerator, VisionModeratorError


class FakeCloud:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "poppy_relevant": True,
            "is_edit": True,
            "insults_poppy": False,
            "insults_other_games": False,
            "high_effort": True,
            "vision_confidence": 0.91,
            "vision_model": "@cf/meta/llama-3.2-11b-vision-instruct",
            "human_review_required": True,
            "audio_review_required": True,
            "auto_approved": False,
            "publishing": False,
        }
        self.error = error
        self.calls = []

    def moderate_frame(self, image_data_url, *, platform, title="", caption=""):
        self.calls.append({
            "image_data_url": image_data_url,
            "platform": platform,
            "title": title,
            "caption": caption,
        })
        if self.error:
            raise self.error
        return dict(self.result)


class VisionModeratorTests(unittest.TestCase):
    def candidate(self):
        return Candidate(
            platform="youtube",
            canonical_url="https://www.youtube.com/watch?v=vkVFgmgLo1s",
            external_id="vkVFgmgLo1s",
            title="Poppy Playtime fan edit",
            caption="#PoppyPlaytime #edit",
            source_key="UC_allowed",
            media_type="video",
        )

    @staticmethod
    def inspection(_path, _kind=None):
        return InspectionResult(
            ok=True, media_type="video", sha256="a" * 64,
            size_bytes=1000000, mime_type="video/mp4",
            width=1920, height=1080, duration_seconds=10.0,
            has_audio=True, video_codec="h264", audio_codec="aac",
        )

    @staticmethod
    def fake_ffmpeg(command, **kwargs):
        output = Path(command[-1])
        Image.new("RGB", (480, 480), (80, 100, 120)).save(output, format="JPEG")
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_builds_contact_sheet_and_returns_visual_signals_but_not_music(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder test file")
            cloud = FakeCloud()
            moderator = CloudflareVisionModerator(
                cloud, inspector=self.inspection, minimum_confidence=0.7
            )
            with patch("agent.brain.vision_moderator.subprocess.run", side_effect=self.fake_ffmpeg) as ffmpeg:
                result = moderator(self.candidate(), video)

        self.assertEqual(ffmpeg.call_count, 4)
        self.assertTrue(cloud.calls[0]["image_data_url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(cloud.calls[0]["platform"], "youtube")
        self.assertTrue(result["poppy_relevant"])
        self.assertTrue(result["high_effort"])
        self.assertNotIn("has_music", result)
        self.assertTrue(result["audio_review_required"])
        self.assertTrue(result["human_review_required"])

    def test_injectable_audio_checker_can_supply_music_signal(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder test file")
            moderator = CloudflareVisionModerator(
                FakeCloud(),
                inspector=self.inspection,
                audio_moderator=lambda _path: True,
            )
            with patch("agent.brain.vision_moderator.subprocess.run", side_effect=self.fake_ffmpeg):
                result = moderator(self.candidate(), video)
        self.assertIs(result["has_music"], True)
        self.assertFalse(result["audio_review_required"])

    def test_low_confidence_withholds_visual_decisions(self):
        model_result = {
            "poppy_relevant": True, "is_edit": True, "insults_poppy": False,
            "insults_other_games": False, "high_effort": True, "vision_confidence": 0.42,
        }
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder test file")
            moderator = CloudflareVisionModerator(
                FakeCloud(result=model_result), inspector=self.inspection, minimum_confidence=0.7
            )
            with patch("agent.brain.vision_moderator.subprocess.run", side_effect=self.fake_ffmpeg):
                result = moderator(self.candidate(), video)
        self.assertNotIn("poppy_relevant", result)
        self.assertEqual(result["vision_confidence"], 0.42)

    def test_missing_ffmpeg_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder test file")
            moderator = CloudflareVisionModerator(FakeCloud(), inspector=self.inspection)
            with patch(
                "agent.brain.vision_moderator.subprocess.run",
                side_effect=FileNotFoundError(),
            ):
                with self.assertRaises(VisionModeratorError) as caught:
                    moderator(self.candidate(), video)
        self.assertEqual(caught.exception.code, "ffmpeg_not_installed")

    def test_model_errors_do_not_create_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder test file")
            moderator = CloudflareVisionModerator(
                FakeCloud(error=RuntimeError("private model response")), inspector=self.inspection
            )
            with patch("agent.brain.vision_moderator.subprocess.run", side_effect=self.fake_ffmpeg):
                with self.assertRaises(VisionModeratorError) as caught:
                    moderator(self.candidate(), video)
        self.assertNotIn("private model response", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
