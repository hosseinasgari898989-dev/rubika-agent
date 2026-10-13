from __future__ import annotations

import unittest

from agent.discovery.instagram import discover_official_hashtag_urls


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class InstagramGraphAPITests(unittest.TestCase):
    def test_missing_credentials_does_not_make_network_requests(self):
        session = FakeSession([])
        report = discover_official_hashtag_urls(
            access_token="",
            ig_user_id="",
            session=session,
            hashtags=("poppyplaytime",),
        )
        self.assertEqual(report.candidates, ())
        self.assertEqual(session.calls, [])
        self.assertEqual(report.attempts[0].result, "not_configured")

    def test_reads_only_canonical_instagram_media_and_keeps_media_type(self):
        token = "test-secret-do-not-log"
        session = FakeSession([
            FakeResponse(200, {"data": [{"id": "hashtag-123", "name": "poppyplaytime"}]}),
            FakeResponse(200, {"data": [
                {
                    "id": "media-1",
                    "caption": "Poppy Playtime edit",
                    "media_type": "VIDEO",
                    "permalink": "https://www.instagram.com/reel/AbCdEf_1234/",
                },
                {
                    "id": "media-2",
                    "caption": "Poppy Playtime gallery",
                    "media_type": "CAROUSEL_ALBUM",
                    "permalink": "https://www.instagram.com/p/ZyXwVu_9876/",
                },
                {
                    "id": "media-3",
                    "media_type": "VIDEO",
                    "permalink": "https://example.com/reel/BadLink_123",
                },
                {
                    "id": "media-4",
                    "media_type": "STORY",
                    "permalink": "https://www.instagram.com/p/StoryX_1234/",
                },
            ]}),
        ])
        report = discover_official_hashtag_urls(
            access_token=token,
            ig_user_id="123456789",
            session=session,
            hashtags=("poppyplaytime",),
            sleep_seconds=0,
        )
        self.assertEqual([item.external_id for item in report.candidates], [
            "AbCdEf_1234", "ZyXwVu_9876"
        ])
        self.assertEqual([item.media_type for item in report.candidates], [
            "video", "carousel_album"
        ])
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(all(call[1]["timeout"] == 10 for call in session.calls))
        self.assertNotIn(token, repr(report.report()))

    def test_permission_error_is_redacted_and_stops_followup_queries(self):
        token = "test-secret-do-not-log"
        session = FakeSession([
            FakeResponse(403, {"error": {"message": f"invalid token {token}"}})
        ])
        report = discover_official_hashtag_urls(
            access_token=token,
            ig_user_id="123456789",
            session=session,
            hashtags=("poppyplaytime", "catnap"),
            sleep_seconds=0,
        )
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(report.candidates, ())
        self.assertEqual(report.attempts[0].result, "hashtag_search_http_403")
        self.assertNotIn(token, repr(report.report()))


if __name__ == "__main__":
    unittest.main()
