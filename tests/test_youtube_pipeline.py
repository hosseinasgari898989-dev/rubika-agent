from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.brain.youtube_pipeline import (
    YouTubePolicyError,
    build_ytdlp_command,
    load_youtube_policy,
    normalize_channel_key,
    plan_video_processing,
    select_youtube_channels,
    validate_youtube_video_url,
)


class YouTubePipelinePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_youtube_policy()

    def test_checked_in_policy_is_conservative_and_has_no_channels_implicitly_enabled(self):
        self.assertEqual(self.policy["schema_version"], 1)
        self.assertEqual(self.policy["large_video_threshold_bytes"], 700_000_000)
        self.assertEqual(self.policy["transcription_chunk_seconds"], 900)
        self.assertEqual(self.policy["persian_subtitle_channels"], [])

    def test_normalizes_handles_and_canonical_channel_urls(self):
        self.assertEqual(normalize_channel_key("@My_Channel"), "@my_channel")
        self.assertEqual(normalize_channel_key("My_Channel"), "@my_channel")
        self.assertEqual(normalize_channel_key("https://www.youtube.com/@My_Channel"), "@my_channel")
        self.assertEqual(
            normalize_channel_key("https://www.youtube.com/channel/UCabcdefghijklmnopqrstuvwxyz"),
            "UCabcdefghijklmnopqrstuvwxyz",
        )

    def test_rejects_non_youtube_channel_urls_and_video_urls_as_channel_keys(self):
        for value in (
            "http://www.youtube.com/@channel",
            "https://youtube.com.evil.example/@channel",
            "https://www.youtube.com/watch?v=abcdefghijk",
            "https://user:pass@www.youtube.com/@channel",
        ):
            with self.subTest(value=value), self.assertRaises(YouTubePolicyError):
                normalize_channel_key(value)

    def test_selects_only_enabled_youtube_source_rows_and_deduplicates(self):
        policy = {**self.policy, "persian_subtitle_channels": ["@fa_channel"]}
        sources = [
            {"platform": "youtube", "source_key": "@Fa_Channel", "display_name": "FA channel", "enabled": 1},
            {"platform": "youtube", "source_key": "fa_channel", "display_name": "duplicate", "enabled": True},
            {"platform": "youtube", "source_key": "@muted_channel", "display_name": "Muted", "enabled": 0},
            {"platform": "tiktok", "source_key": "@fa_channel", "display_name": "Wrong platform", "enabled": 1},
        ]
        selected = select_youtube_channels(sources, policy=policy)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["source_key"], "@fa_channel")
        self.assertTrue(selected[0]["requires_persian_subtitles"])

    def test_subtitle_channels_are_normalized_when_policy_is_loaded(self):
        payload = {**self.policy, "persian_subtitle_channels": ["https://www.youtube.com/@My_Channel"]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_youtube_policy(path)
        self.assertEqual(loaded["persian_subtitle_channels"], ["@my_channel"])

    def test_policy_rejects_duplicate_or_malformed_subtitle_channel_keys(self):
        for keys in (["@channel_a", "@CHANNEL_A"], ["https://youtube.com/watch?v=abcdefghijk"]):
            payload = {**self.policy, "persian_subtitle_channels": keys}
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "policy.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(keys=keys), self.assertRaises(YouTubePolicyError):
                    load_youtube_policy(path)

    def test_validates_only_https_youtube_video_urls(self):
        self.assertEqual(
            validate_youtube_video_url("https://www.youtube.com/watch?v=abcdefghijk"),
            "https://www.youtube.com/watch?v=abcdefghijk",
        )
        self.assertEqual(
            validate_youtube_video_url("https://youtu.be/abcdefghijk"),
            "https://youtu.be/abcdefghijk",
        )
        for value in (
            "http://youtube.com/watch?v=abcdefghijk",
            "https://youtube.com.evil.example/watch?v=abcdefghijk",
            "https://youtube.com/playlist?list=PL123",
            "https://user:pass@youtube.com/watch?v=abcdefghijk",
            "https://youtube.com/watch?v=bad",
        ):
            with self.subTest(value=value), self.assertRaises(YouTubePolicyError):
                validate_youtube_video_url(value)

    def test_large_video_plan_uses_http_chunks_and_subtitle_plan_has_bounded_ranges(self):
        policy = {**self.policy, "persian_subtitle_channels": ["@long_channel"]}
        plan = plan_video_processing(
            source_key="@long_channel",
            metadata={"filesize": 800_000_000, "duration": 1900},
            policy=policy,
        )
        self.assertTrue(plan["download"]["large_video"])
        self.assertEqual(plan["download"]["http_chunk_size_bytes"], 10_485_760)
        self.assertTrue(plan["transcription"]["required"])
        self.assertEqual(plan["transcription"]["chunks"], [
            {"index": 1, "start_seconds": 0, "end_seconds": 900},
            {"index": 2, "start_seconds": 900, "end_seconds": 1800},
            {"index": 3, "start_seconds": 1800, "end_seconds": 1900},
        ])

    def test_estimated_size_fallback_and_threshold_boundary(self):
        plan = plan_video_processing(
            source_key="@plain_channel",
            metadata={"filesize": None, "filesize_approx": 700_000_001, "duration": 1800},
            policy=self.policy,
        )
        self.assertTrue(plan["download"]["large_video"])
        self.assertFalse(plan["transcription"]["required"])
        self.assertEqual(plan["transcription"]["chunks"], [])

        equal = plan_video_processing(
            source_key="@plain_channel",
            metadata={"filesize": 700_000_000, "duration": 1800},
            policy=self.policy,
        )
        self.assertFalse(equal["download"]["large_video"])

    def test_rejects_disabled_sources_missing_duration_and_oversized_chunk_plan(self):
        with self.assertRaisesRegex(YouTubePolicyError, "youtube_source_disabled"):
            plan_video_processing(
                source_key="@captioned",
                metadata={"filesize": 10, "duration": 60},
                policy={**self.policy, "persian_subtitle_channels": ["@captioned"]},
                channel_enabled=False,
            )
        with self.assertRaisesRegex(YouTubePolicyError, "youtube_duration_required_for_subtitles"):
            plan_video_processing(
                source_key="@captioned",
                metadata={"filesize": 10},
                policy={**self.policy, "persian_subtitle_channels": ["@captioned"]},
            )
        with self.assertRaisesRegex(YouTubePolicyError, "youtube_transcription_chunk_limit_exceeded"):
            plan_video_processing(
                source_key="@captioned",
                metadata={"filesize": 10, "duration": 100_000},
                policy={**self.policy, "persian_subtitle_channels": ["@captioned"]},
            )

    def test_builds_resumable_ytdlp_argv_and_adds_http_chunk_size_for_large_media(self):
        command = build_ytdlp_command(
            source_url="https://youtu.be/abcdefghijk",
            output_template="/tmp/video.%(ext)s",
            estimated_size_bytes=900_000_000,
            policy=self.policy,
        )
        self.assertIn("--continue", command)
        self.assertIn("--part", command)
        self.assertIn("--http-chunk-size", command)
        self.assertEqual(command[-2:], ["--", "https://youtu.be/abcdefghijk"])
        self.assertNotIn("https://youtu.be/abcdefghijk", command[:-1])

    def test_download_command_rejects_untrusted_urls_and_invalid_paths(self):
        with self.assertRaises(YouTubePolicyError):
            build_ytdlp_command(
                source_url="https://youtube.com.evil.example/watch?v=abcdefghijk",
                output_template="/tmp/video.%(ext)s",
                policy=self.policy,
            )
        with self.assertRaises(YouTubePolicyError):
            build_ytdlp_command(
                source_url="https://youtu.be/abcdefghijk",
                output_template="/tmp/video.%(ext)s\n--exec=evil",
                policy=self.policy,
            )

    def test_rejects_invalid_policy_ranges(self):
        payload = {**self.policy, "concurrent_fragments": 99}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(YouTubePolicyError, "concurrent_fragments_invalid"):
                load_youtube_policy(path)


if __name__ == "__main__":
    unittest.main()
