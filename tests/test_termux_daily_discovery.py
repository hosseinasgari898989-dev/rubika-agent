from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from termux import daily_discovery


class TermuxDailyDiscoveryTests(unittest.TestCase):
    def test_report_combines_tiktok_edits_and_multi_source_official_mob_posts(self):
        tiktok = [{
            "platform": "tiktok",
            "id": "7610592632179297558",
            "url": "https://www.tiktok.com/@creator/video/7610592632179297558",
            "title": "Poppy Playtime edit",
            "caption": "A public edit",
            "published_at": "2026-10-14T11:00:00+00:00",
            "freshness_verified": True,
            "media_type": "video",
        }]
        mob_x = [{
            "platform": "mob_official",
            "id": "x-1734258130186559716",
            "external_id": "x-1734258130186559716",
            "url": "https://x.com/mobgamesstudios/status/1734258130186559716",
            "canonical_url": "https://x.com/mobgamesstudios/status/1734258130186559716",
            "title": "Official Mob update",
            "caption": "Actual official post caption",
            "original_caption": "Actual official post caption",
            "source_platform": "x",
            "source_name": "Mob Entertainment on X",
            "source_key": "x.com/mobgamesstudios",
            "author": "@mobgamesstudios",
            "official_source": True,
            "published_at": "2026-10-14T12:00:00+00:00",
            "freshness_verified": True,
            "media_type": "image",
            "media_url": "https://pbs.twimg.com/media/example.jpg",
        }]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "termux_discovery.json"
            with patch.object(
                daily_discovery, "discover_tiktok_hashtags", return_value=(tiktok, [])
            ), patch.object(
                daily_discovery, "discover_all_official_posts", return_value=(mob_x, [])
            ) as official_search:
                report = daily_discovery.collect_daily_report(output_path=output)

            official_search.assert_called_once_with(max_results=12)
            self.assertEqual(report["summary"]["tiktok_candidates"], 1)
            self.assertEqual(report["source"], "termux_cloud_runner")
            self.assertEqual(report["summary"]["mob_official_candidates"], 1)
            self.assertEqual(report["summary"]["mob_official_source"], "Mob Entertainment official sources")
            self.assertEqual(report["summary"]["paid_providers_used"], [])
            self.assertEqual(report["lifecycle"]["mode"], "test")
            self.assertFalse(report["summary"]["ready_to_publish"])
            self.assertEqual(set(report["candidates"]), {"tiktok", "mob_official"})
            self.assertEqual(report["candidates"]["mob_official"][0]["source_platform"], "x")
            self.assertEqual(
                report["candidates"]["mob_official"][0]["media_url"],
                "https://pbs.twimg.com/media/example.jpg",
            )
            written = output.read_text(encoding="utf-8")
            self.assertNotIn("test-only-token", written)
            self.assertEqual(
                json.loads(written)["candidates"]["mob_official"][0]["id"],
                "x-1734258130186559716",
            )


if __name__ == "__main__":
    unittest.main()
