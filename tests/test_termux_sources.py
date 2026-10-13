from __future__ import annotations

import json
import unittest

from agent.discovery.termux_sources import (
    discover_tiktok_hashtags,
    discover_youtube_metadata,
    extract_tiktok_candidates,
)


class FakeYDL:
    instances = []
    info = {}

    def __init__(self, options):
        self.options = options
        self.queries = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, query, download=False):
        self.queries.append((query, download))
        return self.__class__.info


class TermuxMetadataParserTests(unittest.TestCase):
    def test_tiktok_html_embedded_json_produces_metadata_and_canonical_url(self):
        data = {
            "itemList": [{
                "id": "7610592632179297558",
                "desc": "Poppy Playtime edit #poppyplaytime",
                "createTime": 1791808200,
                "author": {"uniqueId": "example_creator"},
                "stats": {"playCount": 1234, "diggCount": 91, "commentCount": 7, "shareCount": 4},
                "video": {"duration": 14},
            }]
        }
        page = '<html><script id="SIGI_STATE" type="application/json">' + json.dumps(data) + "</script></html>"
        found = extract_tiktok_candidates(page, "#poppyplaytime")
        self.assertEqual(len(found), 1)
        item = found[0]
        self.assertEqual(item["id"], "7610592632179297558")
        self.assertEqual(item["url"], "https://www.tiktok.com/@example_creator/video/7610592632179297558")
        self.assertEqual(item["view_count"], 1234)
        self.assertEqual(item["like_count"], 91)
        self.assertIn("poppyplaytime", item["hashtags"])
        self.assertEqual(item["query"], "#poppyplaytime")

    def test_tiktok_fallback_accepts_only_canonical_video_urls(self):
        page = (
            '<a href="https://www.tiktok.com/@creator/video/7610592632179297558">video</a>'
            '<a href="https://www.tiktok.com/tag/poppyplaytime">tag navigation</a>'
            '<a href="https://evil.example/@creator/video/7610592632179297559">bad host</a>'
        )
        found = extract_tiktok_candidates(page)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["author"], "creator")
        self.assertIsNone(found[0]["view_count"])

    def test_tiktok_default_uses_public_creator_feeds_via_ytdlp_without_download(self):
        FakeYDL.instances = []
        FakeYDL.info = {"entries": [{
            "id": "7610592632179297558",
            "title": "Poppy Playtime edit #poppyplaytime",
            "duration": 18,
            "timestamp": 1791808200,
            "view_count": 12345,
            "like_count": 123,
            "comment_count": 7,
        }]}
        found, attempts = discover_tiktok_hashtags(
            profiles=("example_creator",), max_results=5, sleep_seconds=0,
            ydl_factory=FakeYDL,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(
            found[0]["url"],
            "https://www.tiktok.com/@example_creator/video/7610592632179297558",
        )
        self.assertEqual(found[0]["method"], "termux_ytdlp_public_creator_profiles")
        self.assertEqual(found[0]["view_count"], 12345)
        self.assertEqual(FakeYDL.instances[0].queries[0][1], False)
        self.assertTrue(FakeYDL.instances[0].options["skip_download"])
        self.assertEqual(FakeYDL.instances[0].options["extract_flat"], "in_playlist")
        self.assertEqual(attempts[0]["result"], "metadata_found")

    def test_tiktok_skips_stale_and_undated_profile_items(self):
        FakeYDL.instances = []
        FakeYDL.info = {"entries": [
            {"id": "7610592632179297558", "title": "Poppy Playtime edit", "timestamp": 1775000000},
            {"id": "7610592632179297559", "title": "Poppy Playtime edit"},
        ]}
        found, attempts = discover_tiktok_hashtags(
            profiles=("example_creator",), max_results=5, sleep_seconds=0,
            ydl_factory=FakeYDL,
        )
        self.assertEqual(found, [])
        self.assertEqual(attempts[0]["result"], "no_recent_relevant_public_video_metadata")
        self.assertEqual(attempts[0]["stale_or_undated_skipped"], 2)

    def test_youtube_search_is_metadata_only_and_keeps_only_short_duration(self):
        FakeYDL.instances = []
        FakeYDL.info = {"entries": [
            {
                "id": "vkVFgmgLo1s", "title": "Poppy Playtime edit",
                "duration": 40, "uploader": "Editor", "timestamp": 1791808200,
                "view_count": 12345, "description": "#PoppyPlaytime",
            },
            {"id": "n38wy3o0DhY", "title": "long edit", "duration": 240},
            {"id": "tooShortId", "title": "invalid identifier", "duration": 20},
            {"id": "AbcdefGhijk", "title": "unknown duration", "duration": None},
        ]}
        found, attempts = discover_youtube_metadata(
            queries=("Poppy Playtime edit shorts",),
            max_results=10,
            ydl_factory=FakeYDL,
        )
        self.assertEqual([row["id"] for row in found], ["vkVFgmgLo1s"])
        self.assertEqual(found[0]["duration_seconds"], 40)
        self.assertEqual(found[0]["view_count"], 12345)
        self.assertEqual(FakeYDL.instances[0].queries[0][1], False)
        self.assertTrue(FakeYDL.instances[0].options["skip_download"])
        self.assertTrue(FakeYDL.instances[0].options["extract_flat"])
        self.assertIn("logger", FakeYDL.instances[0].options)
        self.assertTrue(found[0]["freshness_verified"])
        self.assertEqual(attempts[0]["result"], "metadata_found")

    def test_youtube_keeps_undated_flat_search_results_unverified_and_skips_old_dates(self):
        FakeYDL.instances = []
        FakeYDL.info = {"entries": [
            {"id": "vkVFgmgLo1s", "title": "old short", "duration": 40, "timestamp": 1775000000},
            {"id": "n38wy3o0DhY", "title": "undated short", "duration": 24},
        ]}
        found, attempts = discover_youtube_metadata(
            queries=("Poppy Playtime edit shorts",),
            max_results=10,
            ydl_factory=FakeYDL,
        )
        self.assertEqual([row["id"] for row in found], ["n38wy3o0DhY"])
        self.assertIsNone(found[0]["published_at"])
        self.assertFalse(found[0]["freshness_verified"])
        self.assertEqual(found[0]["freshness_status"], "date_unavailable_verify_before_publish")
        self.assertEqual(attempts[0]["result"], "metadata_found")
        self.assertEqual(attempts[0]["stale_or_undated_skipped"], 1)
        self.assertEqual(attempts[0]["freshness_unverified"], 1)

    def test_missing_yt_dlp_reports_a_safe_status(self):
        def missing(_options):
            raise ImportError("not installed")
        found, attempts = discover_youtube_metadata(
            queries=("Poppy Playtime",),
            ydl_factory=missing,
        )
        self.assertEqual(found, [])
        self.assertEqual(attempts[0]["result"], "metadata_search_failed")


if __name__ == "__main__":
    unittest.main()
