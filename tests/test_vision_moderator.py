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
            "vision_confidence": 0.91,
            "vision_model": "@cf/cloudflare/clef-omni",
            "human_review_required": True,
            "auto_approved": False,
            "publishing": False,
        }
        self.error = error
        self.calls = []

    def moderate_video(self, video_data_url, *, platform, title="", caption=""):
        self.calls.append({
            "video_data_url": video_data_url,
            "platform": platform,
            "title": title,
            "caption": caption,
        })
        if self.error:
            raise self.error
        return dict(self.result)


class CloudflareVideoModeratorTests(unittest.TestCase):
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

    def inspection(self, _path, _kind=None, *, duration=10.0):
        return InspectionResult(
            ok=True, media_type="video", sha256="a" * 64,
            size_bytes=1000000, mime_type="video/mp4",
            width=1920, height=1080, duration_seconds=duration,
            has_audio=True, video_codec="h264", audio_codec="aac",
        )

    @staticmethod
    def fake_ffmpeg(command, **kwargs):
        output = Path(command[-1])
        output.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"x" * 70000)
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_downsamples_full_video_and_uses_synchronized_video_audio_api(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder source file")
            cloud = FakeCloud()
            moderator = CloudflareVideoModerator(cloud, inspector=self.inspection)
            with patch("agent.brain.vision_moderator.subprocess.run", side_effect=self.fake_ffmpeg) as ffmpeg:
                result = moderator(self.candidate(), video)

        self.assertEqual(ffmpeg.call_count, 1)
        command = ffmpeg.call_args.args[0]
        self.assertIn("0:a?", command)
        self.assertIn("libx264", command)
        self.assertTrue(cloud.calls[0]["video_data_url"].startswith("data:video/mp4;base64,"))
        self.assertEqual(cloud.calls[0]["platform"], "youtube")
        self.assertIs(result["has_music"], True)
        self.assertEqual(
            set(result).intersection({"poppy_relevant", "is_edit", "high_effort", "insults_poppy", "insults_other_games"}),
            set(),
        )
        self.assertFalse(result["sample_truncated"])
        self.assertTrue(result["human_review_required"])
        self.assertFalse(result["auto_approved"])
        self.assertFalse(result["publishing"])

    def test_videos_over_one_minute_are_marked_as_truncated(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder source file")
            moderator = CloudflareVideoModerator(
                FakeCloud(), inspector=lambda path, kind=None: self.inspection(path, kind, duration=95.0)
            )
            with patch("agent.brain.vision_moderator.subprocess.run", side_effect=self.fake_ffmpeg):
                result = moderator(self.candidate(), video)
        self.assertTrue(result["sample_truncated"])

    def test_low_aggregate_confidence_keeps_thresholded_answers_with_diagnostic(self):
        result_payload = {"has_music": True, "vision_confidence": 0.42}
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder source file")
            moderator = CloudflareVideoModerator(
                FakeCloud(result=result_payload), inspector=self.inspection, minimum_confidence=0.7
            )
            with patch("agent.brain.vision_moderator.subprocess.run", side_effect=self.fake_ffmpeg):
                result = moderator(self.candidate(), video)
        self.assertTrue(result["aggregate_confidence_below_threshold"])
        self.assertFalse(result["moderation_uncertain"])
        self.assertEqual(set(result).intersection({"poppy_relevant", "is_edit", "high_effort"}), set())

    def test_unknown_music_answer_keeps_candidate_unapproved(self):
        result_payload = {"vision_confidence": 0.9}
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "source.mp4"
            video.write_bytes(b"placeholder source file")
            moderator = CloudflareVideoModerator(FakeCloud(result=result_payload), inspector=self.inspection)
            with patch("agent.brain.vision_moderator.subprocess.run", side_effect=self.fake_ffmpeg):
                result = moderator(self.candidate(), video)
        self.assertNotIn("has_music", result)
        self.assertTrue(result["moderation_uncertain"])

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
