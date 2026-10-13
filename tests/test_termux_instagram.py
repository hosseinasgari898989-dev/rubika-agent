from __future__ import annotations

import json
import unittest

from agent.discovery.termux_instagram import discover_instagram_hashtags, _json_media_rows


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class FakeInstagramYDL:
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


class TermuxInstagramTests(unittest.TestCase):
    def test_default_discovery_uses_ytdlp_public_hashtag_metadata_only(self):
        FakeInstagramYDL.instances = []
        FakeInstagramYDL.info = {"entries": [{
            "id": "AbCdEf_1234",
            "webpage_url": "https://www.instagram.com/reel/AbCdEf_1234/",
            "title": "Poppy Playtime Reel #poppyplaytime",
            "duration": 22,
            "timestamp": 1791808200,
            "view_count": 1234,
        }]}
        found, attempts = discover_instagram_hashtags(
            tags=("poppyplaytime",), max_results=5, sleep_seconds=0,
            ydl_factory=FakeInstagramYDL,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["id"], "AbCdEf_1234")
        self.assertEqual(found[0]["method"], "termux_ytdlp_public_hashtag_extractor")
        self.assertTrue(FakeInstagramYDL.instances[0].options["skip_download"])
        self.assertEqual(FakeInstagramYDL.instances[0].queries[0][1], False)
        self.assertEqual(attempts[0]["result"], "metadata_found")

    def test_parses_video_reel_and_rejects_static_image(self):
        payload = [
            {
                "@type": "VideoObject",
                "url": "https://www.instagram.com/reel/AbCdEf_1234/",
                "name": "Poppy Playtime edit",
                "description": "Poppy Playtime #poppyplaytime",
                "datePublished": "2026-10-12T14:00:00Z",
                "author": {"@type": "Person", "name": "Editor"},
            },
            {
                "@type": "ImageObject",
                "url": "https://www.instagram.com/p/ImAgeOnly_123/",
                "name": "Still image",
            },
        ]
        page = (
            '<meta property="og:title" content="Poppy Playtime">'
            '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
        )
        found = _json_media_rows(page, "poppyplaytime")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["id"], "AbCdEf_1234")
        self.assertEqual(found[0]["media_type"], "video")
        self.assertEqual(found[0]["url"], "https://www.instagram.com/reel/AbCdEf_1234/")
        self.assertEqual(found[0]["published_at"], "2026-10-12T14:00:00+00:00")
        self.assertIn("poppyplaytime", found[0]["hashtags"])

    def test_only_canonical_instagram_hosts_are_accepted(self):
        body = (
            '<script type="application/ld+json">'
            + json.dumps([
                {"@type": "VideoObject", "url": "https://evil.example/reel/BadLink_123/"},
                {"@type": "VideoObject", "url": "https://www.instagram.com/reel/GoodLink_123/"},
            ])
            + "</script>"
        )
        found = _json_media_rows(body, "catnap")
        self.assertEqual([item["id"] for item in found], ["GoodLink_123"])

    def test_challenge_or_access_denied_is_not_retried(self):
        session = FakeSession([
            FakeResponse(403, "challenge_required"),
            FakeResponse(200, ""),
        ])
        found, attempts = discover_instagram_hashtags(
            session=session,
            tags=("poppyplaytime", "catnap"),
            sleep_seconds=0,
        )
        self.assertEqual(found, [])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(attempts[0]["result"], "http_403")

    def test_public_empty_page_returns_zero_not_fake_candidates(self):
        session = FakeSession([
            FakeResponse(200, "<html><title>Log in to Instagram</title></html>")
        ])
        found, attempts = discover_instagram_hashtags(
            session=session,
            tags=("poppyplaytime",),
            sleep_seconds=0,
        )
        self.assertEqual(found, [])
        self.assertEqual(attempts[0]["result"], "login_wall_or_challenge")


if __name__ == "__main__":
    unittest.main()
