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


class TermuxInstagramTests(unittest.TestCase):
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
