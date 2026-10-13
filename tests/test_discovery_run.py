from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.discovery import run as runner
from agent.discovery import v7


class DiscoveryOrchestratorTests(unittest.TestCase):
    def test_phone_report_merges_canonical_metadata_without_live_searches(self):
        tiktok_ids = (
            "7616247520657771796",
            "7557483116605033748",
            "7654660335898529038",
            "7610592632179297558",
            "7615602468646407432",
        )
        youtube_ids = (
            "vkVFgmgLo1s",
            "n38wy3o0DhY",
            "AbcdefGhijk",
            "LMNOPQRSTuv",
            "aBcDeFgHiJk",
        )
        phone_report = {
            "schema_version": 1,
            "generated_at": "2026-10-13T12:00:00+00:00",
            "source": "termux_phone",
            "mode": "metadata_only_no_download_no_publish",
            "summary": {},
            "candidates": {
                "tiktok": [
                    {
                        "platform": "tiktok",
                        "id": video_id,
                        "url": f"https://www.tiktok.com/@creator/video/{video_id}",
                        "title": "Poppy Playtime edit",
                        "media_type": "video",
                    }
                    for video_id in tiktok_ids
                ],
                "youtube": [
                    {
                        "platform": "youtube",
                        "id": video_id,
                        "url": f"https://www.youtube.com/shorts/{video_id}",
                        "title": "Poppy Playtime fan edit",
                        "duration_seconds": 45,
                        "media_type": "video",
                    }
                    for video_id in youtube_ids
                ],
                "instagram": [
                    {
                        "platform": "instagram",
                        "id": "AbCdEf_1234",
                        "url": "https://www.instagram.com/reel/AbCdEf_1234/",
                        "title": "Poppy Playtime Instagram Reel",
                        "media_type": "video",
                    }
                ],
            },
            "attempts": [],
        }

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            phone_path = root / "termux.json"
            phone_path.write_text(json.dumps(phone_report), encoding="utf-8")
            with patch.object(
                v7,
                "known_ids",
                return_value={"tiktok": set(), "youtube": set(), "instagram": set()},
            ):
                report = runner.run_discovery(
                    report_path=root / "discovery.json",
                    phone_report_path=phone_path,
                )
            report_text = (root / "discovery.json").read_text(encoding="utf-8")

        self.assertEqual(report["summary"]["tiktok_candidates"], 5)
        self.assertEqual(report["summary"]["youtube_candidates"], 5)
        self.assertEqual(report["summary"]["instagram_candidates"], 1)
        self.assertTrue(report["summary"]["core_search_target_met"])
        self.assertTrue(report["summary"]["termux_report_loaded"])
        self.assertFalse(report["summary"]["ready_to_publish"])
        self.assertEqual(report["candidates"]["instagram"][0]["method"], "termux_phone")
        self.assertTrue(report_text)
        self.assertEqual(
            json.loads(report_text)["mode"],
            "metadata_only_no_download_no_publish",
        )

    def test_invalid_phone_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            invalid = root / "invalid.json"
            invalid.write_text(json.dumps({
                "schema_version": 1,
                "source": "untrusted",
                "mode": "metadata_only_no_download_no_publish",
                "candidates": {
                    "tiktok": [{
                        "platform": "tiktok",
                        "id": "12345678901",
                        "url": "https://evil.example/@creator/video/12345678901",
                    }]
                },
            }), encoding="utf-8")
            with patch.object(
                v7,
                "known_ids",
                return_value={"tiktok": set(), "youtube": set(), "instagram": set()},
            ):
                candidates, generated, status = runner._load_phone_report(invalid)
        self.assertEqual(status, "invalid_report")
        self.assertIsNone(generated)
        self.assertEqual(candidates["tiktok"], [])


if __name__ == "__main__":
    unittest.main()
