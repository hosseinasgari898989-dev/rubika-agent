from __future__ import annotations
import unittest
from agent.discovery.tavily import discover_social_urls

KEY="test-key-never-use-in-real-requests"


class FakeResponse:
    def __init__(self,status_code=200,payload=None):
        self.status_code=status_code
        self._payload=payload if payload is not None else {"results":[],"usage":{"credits":1}}
    def json(self):
        if isinstance(self._payload,Exception):raise self._payload
        return self._payload


class FakeSession:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs))
        if not self.responses:raise AssertionError("Unexpected Tavily request")
        response=self.responses.pop(0)
        if isinstance(response,Exception):raise response
        return response


class TavilyDiscoveryTests(unittest.TestCase):
    def test_missing_key_makes_no_network_request(self):
        session=FakeSession([])
        report=discover_social_urls("instagram",api_key="",session=session,sleep_seconds=0)
        self.assertEqual(report.candidates,());self.assertEqual(session.calls,[])
        self.assertEqual(report.attempts[0].result,"not_configured")

    def test_instagram_urls_are_extracted_and_deduplicated(self):
        session=FakeSession([FakeResponse(payload={"results":[
            {"url":"https://www.instagram.com/reel/AbCdEf_1234/","title":"Poppy Playtime Reel"},
            {"url":"https://www.instagram.com/reel/AbCdEf_1234/?igsh=again","title":"Duplicate"},
            {"url":"https://www.instagram.com/p/ZyXwVu_9876/?igsh=abc","title":"A post"},
        ],"usage":{"credits":1}})])
        report=discover_social_urls("instagram",api_key=KEY,session=session,max_queries=1,sleep_seconds=0)
        self.assertEqual(len(report.candidates),2)
        self.assertEqual(report.candidates[0].canonical_url,"https://www.instagram.com/reel/AbCdEf_1234/")
        self.assertEqual(report.candidates[0].media_type,"unknown")
        self.assertEqual(session.calls[0][1]["json"]["include_domains"],["instagram.com"])

    def test_rejects_lookalike_hosts_and_noncanonical_results(self):
        session=FakeSession([FakeResponse(payload={"results":[
            {"url":"https://instagram.com.example.invalid/reel/AbCdEf_1234/","title":"Lookalike"},
            {"url":"https://example.invalid/?url=https://www.instagram.com/reel/AbCdEf_1234/","title":"External"},
            {"url":"https://www.instagram.com/reel/not-an-id/","title":"Bad ID"},
        ],"usage":{"credits":1}})])
        report=discover_social_urls("instagram",api_key=KEY,session=session,max_queries=1,sleep_seconds=0)
        self.assertEqual(report.candidates,())
        self.assertEqual(report.attempts[0].result,"no_canonical_links")

    def test_auth_failure_stops_without_leaking_key_or_provider_body(self):
        session=FakeSession([FakeResponse(status_code=401,payload={"error":"private provider body"})])
        report=discover_social_urls("instagram",api_key=KEY,session=session,sleep_seconds=0)
        self.assertEqual(len(session.calls),1);self.assertEqual(report.attempts[0].result,"invalid_api_key")
        self.assertNotIn(KEY,repr(report));self.assertNotIn("private provider body",repr(report))

    def test_rate_limit_stops_without_a_second_query(self):
        session=FakeSession([FakeResponse(status_code=429)])
        report=discover_social_urls("instagram",api_key=KEY,session=session,sleep_seconds=0)
        self.assertEqual(len(session.calls),1);self.assertEqual(report.attempts[0].result,"provider_rate_limited")

    def test_result_and_query_limits_are_bounded(self):
        rows=[{"url":"https://www.instagram.com/reel/AbCdEf_1234/","title":"One"},
              {"url":"https://www.instagram.com/p/ZyXwVu_9876/","title":"Two"},
              {"url":"https://www.instagram.com/reel/QwErTy_9876/","title":"Three"}]
        session=FakeSession([FakeResponse(payload={"results":rows,"usage":{"credits":1}})])
        report=discover_social_urls("instagram",api_key=KEY,session=session,max_results=2,max_queries=10,sleep_seconds=0)
        self.assertEqual(len(report.candidates),2);self.assertEqual(len(session.calls),1)

    def test_tiktok_and_youtube_are_unsupported_by_tavily(self):
        for platform in ("tiktok","youtube"):
            with self.subTest(platform=platform),self.assertRaises(ValueError):
                discover_social_urls(platform,api_key=KEY,session=FakeSession([]))


if __name__=="__main__":unittest.main()
