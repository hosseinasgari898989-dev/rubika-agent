from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from termux import daily_discovery
from agent.source_policy import resolve_source_policy, set_dynamic_tiktok_sources
from agent.discovery.termux_sources import TIKTOK_PROFILES


class TermuxDailyDiscoveryTests(unittest.TestCase):
    def test_report_combines_tiktok_edits_and_x_only_official_mob_posts(self):
        tiktok = [{
            "platform": "tiktok",
            "id": "7610592632179297558",
            "url": "https://www.tiktok.com/@meloaep_/video/7610592632179297558",
            "author": "meloaep_",
            "hashtags": ["#PoppyPlaytime"],
            "source_policy": resolve_source_policy("tiktok", "meloaep_"),
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
            "published_at": "2026-10-20T00:00:00+00:00",
            "freshness_verified": True,
            "media_type": "image",
            "media_url": "https://pbs.twimg.com/media/example.jpg",
        }]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "termux_discovery.json"
            with patch.object(
                daily_discovery, "discover_tiktok_profiles", return_value=(tiktok, [])
            ) as tiktok_search, patch.object(
                daily_discovery, "discover_official_x_posts", return_value=(mob_x, [])
            ) as official_search:
                report = daily_discovery.collect_daily_report(output_path=output)

            tiktok_search.assert_called_once_with(
                profiles=TIKTOK_PROFILES,
                selected_profiles=TIKTOK_PROFILES,
                max_results=48,
                max_per_profile=8,
                created_after_by_profile={},
            )
            official_search.assert_called_once_with(max_results=12)
            self.assertEqual(report["source"], "github_actions_termux_docker")
            self.assertEqual(report["summary"]["tiktok_candidates"], 1)
            self.assertEqual(report["summary"]["mob_official_candidates"], 1)
            self.assertEqual(report["summary"]["mob_official_source"], "Mob Entertainment official sources")
            self.assertEqual(report["summary"]["paid_providers_used"], [])
            self.assertEqual(report["lifecycle"]["mode"], "production")
            self.assertFalse(report["summary"]["ready_to_publish"])
            self.assertEqual(set(report["candidates"]), {"tiktok", "mob_official"})
            self.assertEqual(report["candidates"]["mob_official"][0]["source_platform"], "x")
            self.assertEqual(report["limits"]["mob_official_source_lanes"], ["x"])
            self.assertEqual(report["summary"]["mob_official_method"], "official_x_syndication_and_verified_seed_urls")
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

    def test_live_collection_loads_console_managed_profiles_through_authenticated_api(self):
        source_rows = [{
            "id": "tiktok:newcreator", "platform": "tiktok",
            "source_key": "newcreator", "enabled": 1, "managed": True,
            "created_at": "2026-11-05T10:00:00Z",
        }]
        candidate = {
            "platform": "tiktok", "id": "7610592632179297558",
            "url": "https://www.tiktok.com/@newcreator/video/7610592632179297558",
            "author": "newcreator", "hashtags": ["#PoppyPlaytime"],
            "title": "Poppy Playtime edit", "caption": "A public edit",
            "published_at": "2026-11-05T11:00:00+00:00", "media_type": "video",
        }
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"AGENT_API_TOKEN": "test-token-that-is-never-real"}), patch.object(daily_discovery, "CloudState") as cloud_type, patch.object(daily_discovery, "discover_tiktok_profiles") as discover, patch.object(daily_discovery, "discover_official_x_posts", return_value=([], [])):
            cloud_type.return_value.get_console_tiktok_sources.return_value = source_rows
            def collect(**kwargs):
                self.assertEqual(kwargs["profiles"], ("newcreator",))
                self.assertEqual(kwargs["created_after_by_profile"], {"newcreator": source_rows[0]["created_at"]})
                return [{**candidate, "source_policy": resolve_source_policy("tiktok", "newcreator")}], []
            discover.side_effect = collect
            report = daily_discovery.collect_daily_report(
                output_path=Path(folder) / "report.json", load_remote_sources=True
            )
        cloud_type.assert_called_once()
        self.assertEqual(report["summary"]["tiktok_candidates"], 1)
        self.assertEqual(report["candidates"]["tiktok"][0]["author"], "newcreator")
        set_dynamic_tiktok_sources([])

    def test_manual_cli_cannot_push_a_report_to_github(self):
        self.assertFalse(hasattr(daily_discovery, "push_report_to_main_branch"))
        with patch.object(daily_discovery.argparse.ArgumentParser, "error", side_effect=SystemExit(2)), \
             patch.object(daily_discovery, "collect_daily_report") as collect:
            with self.assertRaises(SystemExit) as result:
                daily_discovery.main(["--push"])
        self.assertEqual(result.exception.code, 2)
        collect.assert_not_called()



if __name__ == "__main__":
    unittest.main()
