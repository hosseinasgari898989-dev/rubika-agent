from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from termux import daily_discovery


class TermuxDailyDiscoveryTests(unittest.TestCase):
    def test_report_combines_free_metadata_without_downloading_or_publishing(self):
        tiktok = [{
            "platform": "tiktok",
            "id": "7610592632179297558",
            "url": "https://www.tiktok.com/@creator/video/7610592632179297558",
            "title": "Poppy Playtime edit",
            "media_type": "video",
        }]
        instagram = [{
            "platform": "instagram",
            "id": "AbCdEf_1234",
            "url": "https://www.instagram.com/reel/AbCdEf_1234/",
            "title": "Poppy Playtime Reel",
            "media_type": "video",
        }]
        youtube = [{
            "platform": "youtube",
            "id": "vkVFgmgLo1s",
            "url": "https://www.youtube.com/shorts/vkVFgmgLo1s",
            "title": "Poppy Playtime Short",
            "media_type": "video",
        }]
        mob_official = [{
            "platform": "mob_official",
            "id": "youtube:abcdefghijk",
            "url": "https://www.youtube.com/watch?v=abcdefghijk",
            "canonical_url": "https://www.youtube.com/watch?v=abcdefghijk",
            "title": "Official Mob trailer",
            "source_platform": "youtube",
            "official_source": True,
            "media_type": "video",
        }]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "termux_discovery.json"
            with patch.object(
                daily_discovery, "discover_tiktok_hashtags", return_value=(tiktok, [])
            ), patch.object(
                daily_discovery, "discover_instagram_hashtags", return_value=(instagram, [])
            ) as instagram_search, patch.object(
                daily_discovery, "discover_youtube_metadata", return_value=(youtube, [])
            ), patch.object(
                daily_discovery, "discover_mob_official", return_value=(mob_official, [])
            ):
                report = daily_discovery.collect_daily_report(output_path=output)

            instagram_search.assert_called_once()
            self.assertEqual(report["summary"]["tiktok_candidates"], 1)
            self.assertEqual(report["summary"]["youtube_candidates"], 1)
            self.assertEqual(report["summary"]["instagram_candidates"], 1)
            self.assertEqual(report["summary"]["mob_official_candidates"], 1)
            self.assertEqual(report["summary"]["paid_providers_used"], [])
            self.assertEqual(report["candidates"]["mob_official"][0]["source_platform"], "youtube")
            self.assertEqual(report["summary"]["tiktok_method"], "yt_dlp_owner_selected_profiles_plus_public_hashtag_pages")
            self.assertEqual(report["summary"]["instagram_method"], "public_search_index_bing_rss_ddg_fallback")
            self.assertFalse(report["summary"]["ready_to_publish"])
            self.assertEqual(report["mode"], "metadata_only_no_download_no_publish")
            written = output.read_text(encoding="utf-8")
            self.assertNotIn("test-only-token", written)
            self.assertEqual(
                json.loads(written)["candidates"]["instagram"][0]["id"],
                "AbCdEf_1234",
            )

    def test_push_refuses_to_publish_report_from_main_branch(self):
        calls = []

        def fake_git(args):
            calls.append(args)
            if args == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(args, 0, "main\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        with patch.object(daily_discovery, "_git", side_effect=fake_git):
            with self.assertRaisesRegex(RuntimeError, "wrong_branch"):
                daily_discovery.push_report_to_experimental_branch()
        self.assertEqual(calls, [["branch", "--show-current"]])


if __name__ == "__main__":
    unittest.main()
