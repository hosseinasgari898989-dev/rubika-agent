from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from agent.discovery.termux_sources import discover_tiktok_hashtags
from agent.discovery.tiktok_search_index import (
    _tiktok_video_url,
    discover_tiktok_creator_handles,
)


class FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class FakeSearchSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class SearchAwareYDL:
    instances = []

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
        if "/tag/" in query:
            return {"entries": []}
        timestamp = int(datetime.now(timezone.utc).timestamp())
        if "known_creator" in query:
            return {"entries": [{
                "id": "7691234567890123456",
                "webpage_url": "https://www.tiktok.com/@known_creator/video/7691234567890123456",
                "title": "Poppy Playtime edit",
                "timestamp": timestamp,
            }]}
        if "new_editor" in query:
            return {"entries": [{
                "id": "7691234567890123457",
                "webpage_url": "https://www.tiktok.com/@new_editor/video/7691234567890123457",
                "title": "Poppy Playtime fan edit #poppyplaytime",
                "timestamp": timestamp,
                "duration": 18,
                "view_count": 1000,
            }]}
        return {"entries": []}


class TikTokSearchIndexTests(unittest.TestCase):
    def test_canonical_link_parser_rejects_non_tiktok_and_non_video_pages(self):
        self.assertEqual(
            _tiktok_video_url("https://www.tiktok.com/@new_editor/video/7691234567890123457"),
            (
                "new_editor", "7691234567890123457",
                "https://www.tiktok.com/@new_editor/video/7691234567890123457",
            ),
        )
        self.assertIsNone(_tiktok_video_url("https://evil.example/@new_editor/video/7691234567890123457"))
        self.assertIsNone(_tiktok_video_url("https://www.tiktok.com/tag/poppyplaytime"))
        self.assertIsNone(_tiktok_video_url("https://www.tiktok.com/@user/video/not-a-number"))

    def test_bing_rss_discovers_unknown_handles_but_does_not_use_index_date(self):
        rss = """<?xml version="1.0"?>
        <rss><channel>
          <item><title>Poppy Playtime edit</title>
            <link>https://www.tiktok.com/@new_editor/video/7691234567890123457</link>
            <description>Public Poppy Playtime edit result</description>
            <pubDate>Mon, 12 Oct 2026 12:00:00 GMT</pubDate>
          </item>
          <item><title>Known source result</title>
            <link>https://www.tiktok.com/@known_creator/video/7691234567890123458</link>
          </item>
          <item><title>Wrong host</title>
            <link>https://example.org/@other/video/7691234567890123459</link>
          </item>
        </channel></rss>"""
        session = FakeSearchSession([FakeResponse(200, rss)])
        handles, attempts = discover_tiktok_creator_handles(
            queries=('site:tiktok.com/@ "poppyplaytime" edit',),
            max_handles=8, sleep_seconds=0, session=session,
            known_profiles=("known_creator",),
        )
        self.assertEqual(handles, ["new_editor"])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][1]["params"]["format"], "rss")
        self.assertEqual(attempts[0]["result"], "public_video_links_found")
        self.assertEqual(attempts[0]["new_handles"], 1)

    def test_search_fallback_does_not_bypass_a_blocked_public_index(self):
        session = FakeSearchSession([
            FakeResponse(200, "<rss><channel></channel></rss>"),
            FakeResponse(202, "public search temporarily unavailable"),
        ])
        handles, attempts = discover_tiktok_creator_handles(
            queries=('site:tiktok.com/@ "poppyplaytime" edit',),
            max_handles=8, sleep_seconds=0, session=session,
        )
        self.assertEqual(handles, [])
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(attempts[-1]["http_status"], 202)
        self.assertEqual(attempts[-1]["result"], "http_202")

    def test_unknown_discovery_uses_search_index_then_verifies_public_profile_metadata(self):
        rss = """<rss><channel><item>
        <title>Poppy Playtime edit</title>
        <link>https://www.tiktok.com/@new_editor/video/7691234567890123457</link>
        </item></channel></rss>"""
        search_session = FakeSearchSession([FakeResponse(200, rss)])
        # Four public hashtag HTML pages return ordinary empty HTML in this test.
        empty_html_session = FakeSearchSession([
            FakeResponse(200, "<html>no embedded video data</html>") for _ in range(4)
        ])
        SearchAwareYDL.instances = []
        with patch("agent.discovery.termux_sources.requests.Session", return_value=empty_html_session):
            rows, attempts = discover_tiktok_hashtags(
                profiles=("known_creator",),
                max_results=2,
                include_unknown_sources=True,
                known_source_max_results=1,
                unknown_source_max_results=1,
                max_per_profile=1,
                sleep_seconds=0,
                ydl_factory=SearchAwareYDL,
                search_session=search_session,
            )
        self.assertEqual(len(rows), 2)
        unknown = [row for row in rows if row.get("author") == "new_editor"]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]["method"], "termux_ytdlp_public_search_index_creator_profiles")
        self.assertEqual(unknown[0]["discovery_origin"], "public_search_index")
        self.assertEqual(unknown[0]["creator_trust_tier"], "unknown")
        self.assertTrue(unknown[0]["published_at"])
        self.assertTrue(any(attempt.get("method") == "bing_rss_search_index" for attempt in attempts))
        # All yt-dlp requests remain metadata-only; no media bytes are fetched here.
        self.assertTrue(all(not download for instance in SearchAwareYDL.instances for _, download in instance.queries))


if __name__ == "__main__":
    unittest.main()
