from __future__ import annotations

import unittest

from agent.discovery.tavily import discover_social_urls


KEY = "test-key-never-use-in-real-requests"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"results": [], "usage": {"credits": 1}}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected Tavily request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TavilyDiscoveryTests(unittest.TestCase):
    def test_missing_key_makes_no_network_request(self):
        session = FakeSession([])
        report = discover_social_urls("tiktok", api_key="", session=session, sleep_seconds=0)
        self.assertEqual(report.candidates, ())
        self.assertEqual(len(session.calls), 0)
        self.assertEqual(report.attempts[0].result, "not_configured")

    def test_tiktok_canonical_urls_are_extracted_and_deduplicated(self):
        session = FakeSession([
            FakeResponse(payload={
                "results": [
                    {
                        "url": "https://www.tiktok.com/@creator/video/7616247520657771796?share=1",
                        "title": "Poppy Playtime Huggy Wuggy edit",
                    },
                    {
                        "url": "https://www.tiktok.com/@creator/video/7616247520657771796",
                        "title": "Duplicate result",
                    },
                    {
                        "url": "https://tiktok.com/@other/video/7557483116605033748",
                        "title": "CatNap edit",
                    },
                ],
                "usage": {"credits": 1},
            }),
        ])
        report = discover_social_urls(
            "tiktok", api_key=KEY, session=session, max_queries=1, sleep_seconds=0
        )
        self.assertEqual(len(report.candidates), 2)
        self.assertEqual(
            report.candidates[0].canonical_url,
            "https://www.tiktok.com/@creator/video/7616247520657771796",
        )
        self.assertEqual(report.candidates[0].external_id, "7616247520657771796")
        self.assertEqual(report.candidates[0].source_key, "@creator")
        self.assertEqual(report.attempts[0].credits_used, 1)
        self.assertEqual(session.calls[0][1]["headers"]["Authorization"], "Bearer " + KEY)
        self.assertEqual(session.calls[0][1]["json"]["include_domains"], ["tiktok.com"])

    def test_instagram_reel_and_post_are_extracted(self):
        session = FakeSession([
            FakeResponse(payload={
                "results": [
                    {
                        "url": "https://www.instagram.com/reel/AbCdEf_1234/",
                        "title": "Poppy Playtime Reel",
                    },
                    {
                        "url": "https://www.instagram.com/p/ZyXwVu_9876/?igsh=abc",
                        "title": "Official-looking post",
                    },
                ],
                "usage": {"credits": 1},
            }),
        ])
        report = discover_social_urls(
            "instagram", api_key=KEY, session=session, max_queries=1, sleep_seconds=0
        )
        self.assertEqual(len(report.candidates), 2)
        self.assertEqual(report.candidates[0].canonical_url, "https://www.instagram.com/reel/AbCdEf_1234/")
        self.assertEqual(report.candidates[0].media_type, "unknown")
        self.assertEqual(session.calls[0][1]["json"]["include_domains"], ["instagram.com"])

    def test_rejects_lookalike_hosts_and_noncanonical_results(self):
        session = FakeSession([
            FakeResponse(payload={
                "results": [
                    {
                        "url": "https://tiktok.com.example.invalid/@x/video/7616247520657771796",
                        "title": "Lookalike host",
                    },
                    {
                        "url": "https://example.invalid/?url=https://www.tiktok.com/@x/video/7616247520657771796",
                        "title": "External page mentioning TikTok",
                    },
                    {
                        "url": "https://www.tiktok.com/@x/video/not-a-number",
                        "title": "Not a video ID",
                    },
                ],
                "usage": {"credits": 1},
            }),
        ])
        report = discover_social_urls(
            "tiktok", api_key=KEY, session=session, max_queries=1, sleep_seconds=0
        )
        self.assertEqual(report.candidates, ())
        self.assertEqual(report.attempts[0].result, "no_canonical_links")

    def test_auth_failure_stops_without_leaking_api_key(self):
        session = FakeSession([FakeResponse(status_code=401, payload={"error": "private provider body"})])
        report = discover_social_urls("tiktok", api_key=KEY, session=session, sleep_seconds=0)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(report.attempts[0].result, "invalid_api_key")
        self.assertNotIn(KEY, repr(report))
        self.assertNotIn("private provider body", repr(report))

    def test_rate_limit_stops_and_does_not_retry_second_query(self):
        session = FakeSession([FakeResponse(status_code=429)])
        report = discover_social_urls("instagram", api_key=KEY, session=session, sleep_seconds=0)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(report.attempts[0].result, "provider_rate_limited")

    def test_query_and_result_limits_are_bounded(self):
        payload = {
            "results": [
                {"url": "https://www.tiktok.com/@creator/video/7616247520657771796", "title": "One"},
                {"url": "https://www.tiktok.com/@creator/video/7557483116605033748", "title": "Two"},
                {"url": "https://www.tiktok.com/@creator/video/7654660335898529038", "title": "Three"},
            ],
            "usage": {"credits": 1},
        }
        session = FakeSession([FakeResponse(payload=payload)])
        report = discover_social_urls(
            "tiktok", api_key=KEY, session=session, max_results=2,
            max_queries=10, sleep_seconds=0,
        )
        self.assertEqual(len(report.candidates), 2)
        self.assertEqual(len(session.calls), 1)
        self.assertLessEqual(len(session.calls), 2)

    def test_unsupported_platform_is_rejected(self):
        with self.assertRaises(ValueError):
            discover_social_urls("youtube", api_key=KEY, session=FakeSession([]))


if __name__ == "__main__":
    unittest.main()
