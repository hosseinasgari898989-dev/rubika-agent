from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.discovery import run as runner
from agent.discovery import v7
from agent.discovery.instagram import InstagramCandidate, InstagramDiscovery


class DiscoveryOrchestratorTests(unittest.TestCase):
    def test_unified_report_merges_sources_but_never_marks_it_publish_ready(self):
        def fill_tiktok(store, seen):
            for video_id in (
                "7616247520657771796", "7557483116605033748", "7654660335898529038",
                "7610592632179297558", "7615602468646407432",
            ):
                store["tiktok"][video_id] = {
                    "platform": "tiktok", "id": video_id,
                    "url": f"https://www.tiktok.com/@creator/video/{video_id}",
                    "title": "Poppy Playtime edit",
                }
                seen.add(video_id)

        def fill_youtube(store, seen):
            for video_id in ("vkVFgmgLo1s", "n38wy3o0DhY", "AbcdefGhijk", "LMNOPQRSTuv", "aBcDeFgHiJk"):
                store["youtube"][video_id] = {
                    "platform": "youtube", "id": video_id,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "title": "Poppy Playtime fan edit",
                }
                seen.add(video_id)

        instagram_candidate = InstagramCandidate(
            platform="instagram",
            external_id="AbCdEf_1234",
            canonical_url="https://www.instagram.com/reel/AbCdEf_1234/",
            post_type="reel",
            query="Poppy Playtime edit",
            method="test",
        )

        with tempfile.TemporaryDirectory() as folder, \
             patch.object(v7, "known_ids", return_value={"tiktok": set(), "youtube": set()}), \
             patch.object(v7, "tiktok_from_tikwm", side_effect=fill_tiktok), \
             patch.object(v7, "tiktok_from_search_engines", return_value=None), \
             patch.object(v7, "discover_youtube_ytdlp", side_effect=fill_youtube), \
             patch.object(v7, "discover_youtube_html", return_value=None), \
             patch.object(
                 runner, "discover_public_indexed_urls",
                 return_value=InstagramDiscovery((instagram_candidate,), ()),
             ):
            report = runner.run_discovery(report_path=Path(folder) / "discovery.json")

        self.assertEqual(report["summary"]["tiktok_candidates"], 5)
        self.assertEqual(report["summary"]["youtube_candidates"], 5)
        self.assertEqual(report["summary"]["instagram_candidates"], 1)
        self.assertFalse(report["summary"]["ready_to_publish"])
        self.assertTrue((Path(folder) / "discovery.json").is_file())
        self.assertEqual(json.loads((Path(folder) / "discovery.json").read_text())["mode"],
                         "public_discovery_only_no_download_no_publish")


if __name__ == "__main__":
    unittest.main()
