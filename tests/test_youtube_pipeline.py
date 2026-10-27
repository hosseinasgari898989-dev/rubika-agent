from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.brain.youtube_pipeline import (
    YouTubePolicyError,
    assess_download_disk_budget,
    build_ffmpeg_normalize_command,
    build_ytdlp_command,
    check_rubika_upload_size,
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

    def test_checked_in_policy_is_conservative_and_upload_limit_is_unverified(self):
        self.assertEqual(self.policy["schema_version"], 1)
        self.assertEqual(self.policy["http_chunk_size_bytes"], 10_485_760)
        self.assertEqual(self.policy["transcription_chunk_seconds"], 900)
        self.assertEqual(self.policy["output_short_edge_max"], 720)
        self.assertEqual(self.policy["output_long_edge_max"], 1280)
        self.assertEqual(self.policy["persian_subtitle_channels"], [])
        self.assertIsNone(self.policy["rubika_upload_limit_bytes"])

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

    def test_large_video_plan_uses_chunks_and_bounded_transcription_ranges(self):
        policy = {**self.policy, "persian_subtitle_channels": ["@long_channel"]}
        plan = plan_video_processing(
            source_key="@long_channel",
            metadata={"filesize": 800_000_000, "duration": 1900, "width": 1920, "height": 1080},
            policy=policy,
            available_disk_bytes=5_000_000_000,
        )
        self.assertTrue(plan["download"]["http_chunking_enabled"])
        self.assertEqual(plan["download"]["http_chunk_size_bytes"], 10_485_760)
        self.assertTrue(plan["download"]["disk_budget"]["ok"])
        self.assertEqual(plan["output"]["max_width"], 1280)
        self.assertEqual(plan["output"]["max_height"], 720)
        self.assertTrue(plan["output"]["requires_ffmpeg_normalization"])
        self.assertTrue(plan["transcription"]["required"])
        self.assertEqual(plan["transcription"]["chunks"], [
            {"index": 1, "start_seconds": 0, "end_seconds": 900},
            {"index": 2, "start_seconds": 900, "end_seconds": 1800},
            {"index": 3, "start_seconds": 1800, "end_seconds": 1900},
        ])

    def test_estimated_size_is_observed_not_a_fixed_download_cutoff(self):
        plan = plan_video_processing(
            source_key="@plain_channel",
            metadata={"filesize": None, "filesize_approx": 700_000_001, "duration": 1800},
            policy=self.policy,
        )
        self.assertTrue(plan["download"]["http_chunking_enabled"])
        self.assertFalse(plan["transcription"]["required"])
        self.assertEqual(plan["transcription"]["chunks"], [])
        self.assertEqual(plan["download"]["disk_budget"]["status"], "not_checked")

        equal = plan_video_processing(
            source_key="@plain_channel",
            metadata={"filesize": 700_000_000, "duration": 1800},
            policy=self.policy,
        )
        self.assertTrue(equal["download"]["http_chunking_enabled"])

    def test_disk_budget_allows_one_gigabyte_only_when_space_is_sufficient(self):
        accepted = assess_download_disk_budget(
            source_size_bytes=1_000_000_000,
            available_disk_bytes=5_000_000_000,
            policy=self.policy,
        )
        rejected = assess_download_disk_budget(
            source_size_bytes=1_000_000_000,
            available_disk_bytes=4_000_000_000,
            policy=self.policy,
        )
        unknown = assess_download_disk_budget(
            source_size_bytes=None,
            available_disk_bytes=8_000_000_000,
            policy=self.policy,
        )
        self.assertTrue(accepted["ok"])
        self.assertGreaterEqual(accepted["required_bytes"], 4_000_000_000)
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["status"], "insufficient_disk_space")
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["status"], "source_size_unknown")

    def test_large_download_has_no_arbitrary_total_size_cap(self):
        plan = plan_video_processing(
            source_key="@one_gig_channel",
            metadata={"filesize": 1_000_000_000, "duration": 3600},
            policy=self.policy,
            available_disk_bytes=6_000_000_000,
        )
        self.assertTrue(plan["download"]["http_chunking_enabled"])
        self.assertFalse(plan["download"]["has_fixed_total_size_cap"])
        self.assertTrue(plan["download"]["resume_partial_downloads"])
        command = build_ytdlp_command(
            source_url="https://youtu.be/abcdefghijk",
            output_template="/tmp/one-gig.%(ext)s",
            estimated_size_bytes=1_000_000_000,
            policy=self.policy,
        )
        self.assertIn("--http-chunk-size", command)
        self.assertIn("--continue", command)
        self.assertNotIn("--max-filesize", command)

    def test_format_selector_caps_landscape_and_portrait_downloads_at_720_class(self):
        landscape = build_ytdlp_command(
            source_url="https://youtu.be/abcdefghijk",
            output_template="/tmp/landscape.%(ext)s",
            source_width=1920,
            source_height=1080,
            policy=self.policy,
        )
        portrait = build_ytdlp_command(
            source_url="https://youtu.be/abcdefghijk",
            output_template="/tmp/portrait.%(ext)s",
            source_width=1080,
            source_height=1920,
            policy=self.policy,
        )
        self.assertIn(
            "bestvideo[width<=1280][height<=720]+bestaudio/best[width<=1280][height<=720]",
            landscape,
        )
        self.assertIn(
            "bestvideo[width<=720][height<=1280]+bestaudio/best[width<=720][height<=1280]",
            portrait,
        )

    def test_ffmpeg_normalization_command_keeps_landscape_and_portrait_in_720_class(self):
        landscape = build_ffmpeg_normalize_command(
            input_path="/tmp/source.mp4",
            output_path="/tmp/final-landscape.mp4",
            source_width=1920,
            source_height=1080,
            policy=self.policy,
        )
        portrait = build_ffmpeg_normalize_command(
            input_path="/tmp/source.mp4",
            output_path="/tmp/final-portrait.mp4",
            source_width=1080,
            source_height=1920,
            policy=self.policy,
        )
        self.assertIn("libx264", landscape)
        self.assertIn("yuv420p", landscape)
        self.assertIn("aac", landscape)
        self.assertIn("min(1280,iw)", landscape[landscape.index("-vf") + 1])
        self.assertIn("min(720,ih)", landscape[landscape.index("-vf") + 1])
        self.assertIn("min(720,iw)", portrait[portrait.index("-vf") + 1])
        self.assertIn("min(1280,ih)", portrait[portrait.index("-vf") + 1])
        self.assertIn("force_original_aspect_ratio=decrease", portrait[portrait.index("-vf") + 1])

    def test_unknown_rubika_upload_limit_blocks_and_known_limit_uses_margin(self):
        unknown = check_rubika_upload_size(10_000_000, policy=self.policy)
        configured = {**self.policy, "rubika_upload_limit_bytes": 50_000_000}
        within_safe_margin = check_rubika_upload_size(45_000_000, policy=configured)
        over_safe_margin = check_rubika_upload_size(45_000_001, policy=configured)
        self.assertFalse(unknown["allowed"])
        self.assertEqual(unknown["status"], "rubika_upload_limit_unverified")
        self.assertTrue(within_safe_margin["allowed"])
        self.assertFalse(over_safe_margin["allowed"])
        self.assertEqual(over_safe_margin["status"], "output_exceeds_rubika_upload_limit")

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
        with self.assertRaises(YouTubePolicyError):
            build_ffmpeg_normalize_command(
                input_path="/tmp/source.mp4",
                output_path="/tmp/final.mkv",
                source_width=1920,
                source_height=1080,
                policy=self.policy,
            )

    def test_rejects_invalid_policy_ranges(self):
        payload = {**self.policy, "concurrent_fragments": 99}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(YouTubePolicyError, "concurrent_fragments_invalid"):
                load_youtube_policy(path)
        payload = {**self.policy, "rubika_upload_limit_bytes": -1}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(YouTubePolicyError, "rubika_upload_limit_bytes_invalid"):
                load_youtube_policy(path)


if __name__ == "__main__":
    unittest.main()
