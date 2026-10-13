from __future__ import annotations

import unittest
from urllib.parse import urlparse

from agent.discovery.proxitok import (
    ProxiTokDiscovery,
    discover_public_rss,
    extract_rss_candidates,
)


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>poppyplaytime</title>
    <item>
      <title>Huggy Wuggy Poppy Playtime Edit</title>
      <description><![CDATA[<p>Public post preview</p>]]></description>
      <link>https://proxitok.example/@creator/video/7616247520657771796</link>
      <guid isPermaLink="false">7616247520657771796</guid>
      <dc:creator>@creator</dc:creator>
    </item>
    <item>
      <title>CatNap Edit</title>
      <link>https://proxitok.example/@another_creator/video/7557483116605033748</link>
      <guid isPermaLink="false">7557483116605033748</guid>
      <dc:creator>@another_creator</dc:creator>
    </item>
  </channel>
</rss>
"""


class FakeResponse:
    def __init__(self, url, status_code=200, body=FEED):
        self.url = url
        self.status_code = status_code
        self.text = body


class FakeSession:
    def __init__(self, *, redirect_to=None, status_code=200, body=FEED):
        self.calls = []
        self.redirect_to = redirect_to
        self.status_code = status_code
        self.body = body

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        final_url = self.redirect_to or url
        return FakeResponse(final_url, self.status_code, self.body)


class ProxiTokRSSTests(unittest.TestCase):
    def test_extracts_only_canonical_video_ids_and_keeps_author(self):
        candidates = extract_rss_candidates(
            FEED, tag="poppyplaytime", instance="https://proxitok.example"
        )
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].external_id, "7616247520657771796")
        self.assertEqual(
            candidates[0].canonical_url,
            "https://www.tiktok.com/@creator/video/7616247520657771796",
        )
        self.assertEqual(candidates[0].source_key, "@creator")
        self.assertEqual(candidates[0].discovery_method, "proxitok_public_rss")

    def test_rejects_unknown_hosts_and_invalid_ids(self):
        xml = """<rss><channel>
          <item><title>x</title><link>https://untrusted.example/@foo/video/7616247520657771796</link><guid>7616247520657771796</guid><dc:creator>@foo</dc:creator></item>
          <item><title>x</title><link>https://proxitok.example/@foo/video/not-an-id</link><guid>invalid</guid><dc:creator>@foo</dc:creator></item>
        </channel></rss>"""
        result = extract_rss_candidates(
            xml, tag="poppyplaytime", instance="https://proxitok.example"
        )
        self.assertEqual(result, [])

    def test_invalid_xml_is_an_empty_result(self):
        self.assertEqual(
            extract_rss_candidates("<rss>", tag="catnap", instance="https://proxitok.example"),
            [],
        )

    def test_fetches_documented_rss_route_and_deduplicates_results(self):
        session = FakeSession()
        report = discover_public_rss(
            instances=("https://proxitok.example",),
            tags=("poppyplaytime", "huggywuggy"),
            session=session,
            sleep_seconds=0,
        )
        self.assertIsInstance(report, ProxiTokDiscovery)
        self.assertEqual(len(report.candidates), 2)
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(session.calls[0][0].endswith("/tag/poppyplaytime/rss"))
        self.assertTrue(session.calls[1][0].endswith("/tag/huggywuggy/rss"))
        self.assertEqual(report.attempts[0].result, "rss_parsed")

    def test_rejects_cross_host_redirects(self):
        session = FakeSession(redirect_to="https://other.example/tag/poppyplaytime/rss")
        report = discover_public_rss(
            instances=("https://proxitok.example",),
            tags=("poppyplaytime",),
            session=session,
            sleep_seconds=0,
        )
        self.assertEqual(report.candidates, ())
        self.assertEqual(report.attempts[0].result, "cross_host_redirect_rejected")


if __name__ == "__main__":
    unittest.main()
