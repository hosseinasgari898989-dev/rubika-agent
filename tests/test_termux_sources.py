from __future__ import annotations

import json
import unittest

from agent.discovery.termux_sources import (
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
        self.assertEqual(attempts[0]["result"], "metadata_found")

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
