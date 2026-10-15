from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.discovery import run as runner
from agent.discovery import v7


class PhoneReportIntegrationTests(unittest.TestCase):
    def test_valid_phone_report_is_used_without_server_side_social_search(self):
        payload = {
            "schema_version": 1,
            "generated_at": "2026-10-13T10:00:00+00:00",
            "source": "termux_phone",
            "mode": "metadata_only_no_download_no_publish",
            "candidates": {
                "tiktok": [{
                    "platform": "tiktok",
                    "id": "7610592632179297558",
                    "url": "https://www.tiktok.com/@creator/video/7610592632179297558",
                    "title": "Poppy edit",
                }],
                "youtube": [{
                    "platform": "youtube",
                    "id": "vkVFgmgLo1s",
                    "url": "https://www.youtube.com/shorts/vkVFgmgLo1s",
                    "title": "Poppy Short",
                    "duration_seconds": 28,
                }],
                "instagram": [{
                    "platform": "instagram",
                    "id": "AbCdEf_1234",
                    "url": "https://www.instagram.com/reel/AbCdEf_1234/",
                    "title": "Poppy Reel",
                    "media_type": "video",
                }],
            },
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            phone_path = root / "termux_discovery.json"
            output_path = root / "discovery_report.json"
            phone_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(v7, "known_ids", return_value={
                "tiktok": set(), "youtube": set(), "instagram": set()
            }), \
                 patch.object(v7, "tiktok_from_tikwm", side_effect=AssertionError("server TikTok search must not run")), \
                 patch.object(v7, "tiktok_from_search_engines", side_effect=AssertionError("server TikTok search must not run")), \
                 patch.object(v7, "discover_youtube_ytdlp", side_effect=AssertionError("server YouTube search must not run")), \
                 patch.object(v7, "discover_youtube_html", side_effect=AssertionError("server YouTube search must not run")), \
                 patch.object(runner, "discover_public_indexed_urls", side_effect=AssertionError("server Instagram index must not run")), \
                 patch.object(runner, "discover_official_hashtag_urls", side_effect=AssertionError("server Instagram API must not run")), \
                 patch.object(runner, "discover_public_rss", side_effect=AssertionError("server RSS must not run")), \
                 patch.object(runner, "discover_social_urls", side_effect=AssertionError("server search API must not run")), \
                 patch.dict(os.environ, {
                     "INSTAGRAM_GRAPH_ACCESS_TOKEN": "",
                     "INSTAGRAM_GRAPH_USER_ID": "",
                     "ALLOW_LEGACY_INSTAGRAM_INDEX": "",
                     "TAVILY_API_KEY": "",
                     "TERMUX_ONLY": "true",
                 }, clear=False):
                report = runner.run_discovery(report_path=output_path, phone_report_path=phone_path)
        self.assertTrue(report["summary"]["termux_report_loaded"])
        self.assertEqual(report["summary"]["tiktok_candidates"], 1)
        self.assertEqual(report["summary"]["youtube_candidates"], 1)
        self.assertEqual(report["summary"]["instagram_candidates"], 1)
        self.assertFalse(report["summary"]["ready_to_publish"])
        self.assertFalse(report["summary"]["core_search_target_met"])

    def test_termux_only_missing_report_does_not_run_hosted_search(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(v7, "known_ids", return_value={
                "tiktok": set(), "youtube": set(), "instagram": set()
            }), \
                 patch.object(v7, "tiktok_from_tikwm", side_effect=AssertionError("hosted TikTok search must not run")), \
                 patch.object(v7, "tiktok_from_search_engines", side_effect=AssertionError("hosted TikTok search must not run")), \
                 patch.object(v7, "discover_youtube_ytdlp", side_effect=AssertionError("hosted YouTube search must not run")), \
                 patch.object(v7, "discover_youtube_html", side_effect=AssertionError("hosted YouTube search must not run")), \
                 patch.object(runner, "discover_public_indexed_urls", side_effect=AssertionError("hosted Instagram index must not run")), \
                 patch.object(runner, "discover_official_hashtag_urls", side_effect=AssertionError("hosted Instagram API must not run")), \
                 patch.object(runner, "discover_public_rss", side_effect=AssertionError("hosted RSS must not run")), \
                 patch.object(runner, "discover_social_urls", side_effect=AssertionError("hosted search API must not run")), \
                 patch.dict(os.environ, {
                     "TERMUX_ONLY": "true",
                     "TAVILY_API_KEY": "",
                     "INSTAGRAM_GRAPH_ACCESS_TOKEN": "",
                     "INSTAGRAM_GRAPH_USER_ID": "",
                     "ALLOW_LEGACY_INSTAGRAM_INDEX": "",
                 }, clear=False):
                report = runner.run_discovery(
                    report_path=root / "discovery.json",
                    phone_report_path=root / "missing-phone-report.json",
                )
        self.assertFalse(report["summary"]["termux_report_loaded"])
        self.assertEqual(report["summary"]["termux_report_status"], "not_found")
        self.assertTrue(report["summary"]["termux_only"])
        self.assertEqual(report["summary"]["tiktok_candidates"], 0)
        self.assertEqual(report["summary"]["youtube_candidates"], 0)
        self.assertFalse(report["summary"]["core_search_target_met"])

    def test_rejects_noncanonical_or_mismatched_candidate_ids(self):
        payload = {
            "schema_version": 1,
            "generated_at": "now",
            "source": "termux_phone",
            "mode": "metadata_only_no_download_no_publish",
            "candidates": {
                "tiktok": [{
                    "platform": "tiktok", "id": "somebody_else",
                    "url": "https://www.tiktok.com/@creator/video/7610592632179297558",
                }],
                "youtube": [],
                "instagram": [{
                    "platform": "instagram", "id": "AbCdEf_1234",
                    "url": "https://evil.example/reel/AbCdEf_1234/", "media_type": "video",
                }],
            },
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "phone.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            accepted, _, status = runner._load_phone_report(path)
        self.assertEqual(status, "loaded")
        self.assertEqual(accepted["tiktok"], [])
        self.assertEqual(accepted["instagram"], [])


if __name__ == "__main__":
    unittest.main()
