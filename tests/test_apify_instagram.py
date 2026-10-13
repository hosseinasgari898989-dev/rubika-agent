from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent.discovery.apify_instagram import discover_instagram_reels


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)


def usage(spent=0.2):
    return FakeResponse(200, {"data": {
        "totalUsageCreditsUsdAfterVolumeDiscount": spent,
        "usageCycle": {
            "startAt": "2026-10-01T00:00:00.000Z",
            "endAt": "2026-10-31T23:59:59.999Z",
        },
    }})


class ApifyInstagramBudgetTests(unittest.TestCase):
    def test_missing_token_makes_no_requests(self):
        client = FakeSession([])
        report = discover_instagram_reels(token="", session=client)
        self.assertEqual(report["status"], "not_configured")
        self.assertEqual(client.calls, [])

    def test_a_second_attempt_on_the_same_utc_day_is_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            state.write_text(json.dumps({
                "schema_version": 1,
                "last_attempt_date_utc": "2026-10-13",
                "last_attempt_status": "completed",
            }), encoding="utf-8")
            client = FakeSession([])
            report = discover_instagram_reels(
                token="private-test-token",
                session=client,
                state_path=state,
                now=datetime(2026, 10, 13, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(report["status"], "daily_limit_reached")
            self.assertEqual(client.calls, [])

    def test_budget_ceiling_blocks_actor_run(self):
        with tempfile.TemporaryDirectory() as folder:
            client = FakeSession([usage(3.51)])
            state = Path(folder) / "state.json"
            report = discover_instagram_reels(
                token="private-test-token",
                session=client,
                state_path=state,
                now=datetime(2026, 10, 13, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(report["status"], "budget_limit_reached")
            self.assertEqual([call[0] for call in client.calls], ["GET"])
            self.assertFalse(state.exists())

    def test_usage_failure_fails_closed_without_actor_run(self):
        with tempfile.TemporaryDirectory() as folder:
            client = FakeSession([FakeResponse(500, {})])
            report = discover_instagram_reels(
                token="private-test-token",
                session=client,
                state_path=Path(folder) / "state.json",
                now=datetime(2026, 10, 13, tzinfo=timezone.utc),
            )
            self.assertEqual(report["status"], "usage_check_failed")
            self.assertEqual([call[0] for call in client.calls], ["GET"])

    def test_actor_fetches_at_most_two_video_reel_metadata_rows(self):
        token = "private-test-token"
        rows = [
            {
                "type": "Video",
                "shortCode": "AbCdEf_1234",
                "id": "123456",
                "url": "https://www.instagram.com/reel/AbCdEf_1234/",
                "caption": "Poppy Playtime #poppyplaytime",
                "hashtags": ["poppyplaytime"],
                "ownerUsername": "example_creator",
                "timestamp": "2026-10-12T08:30:00.000Z",
                "videoDuration": 17.2,
                "videoViewCount": 1200,
                "likesCount": 50,
            },
            {
                "type": "Video",
                "shortCode": "ZyXwVu_9876",
                "url": "https://www.instagram.com/reel/ZyXwVu_9876/",
                "caption": "CatNap edit",
            },
            {
                "type": "Image",
                "url": "https://www.instagram.com/p/ImageOnly_123/",
                "caption": "not a reel",
            },
            {
                "type": "Video",
                "url": "https://evil.example/reel/BadLink_123/",
                "caption": "non-canonical host",
            },
        ]
        with tempfile.TemporaryDirectory() as folder:
            client = FakeSession([usage(0.1), FakeResponse(200, rows)])
            state = Path(folder) / "state.json"
            report = discover_instagram_reels(
                token=token,
                session=client,
                state_path=state,
                now=datetime(2026, 10, 13, tzinfo=timezone.utc),
                max_results=99,
            )
            self.assertEqual(report["status"], "ok")
            self.assertEqual(len(report["candidates"]), 2)
            self.assertEqual(report["candidates"][0]["id"], "AbCdEf_1234")
            self.assertEqual(report["candidates"][0]["view_count"], 1200)
            self.assertEqual([call[0] for call in client.calls], ["GET", "POST"])
            self.assertNotIn(token, client.calls[0][1])
            self.assertNotIn(token, client.calls[1][1])
            self.assertEqual(client.calls[1][2]["json"]["resultsLimit"], 2)
            self.assertEqual(client.calls[1][2]["json"]["resultsType"], "reels")
            self.assertEqual(client.calls[1][2]["headers"]["Authorization"], "Bearer " + token)
            self.assertNotIn(token, repr(report))
            saved = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(saved["last_attempt_date_utc"], "2026-10-13")
            self.assertEqual(saved["result_count"], 2)

    def test_invalid_state_blocks_to_prevent_duplicate_spend(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            state.write_text("{broken", encoding="utf-8")
            client = FakeSession([])
            report = discover_instagram_reels(
                token="private-test-token",
                session=client,
                state_path=state,
                now=datetime(2026, 10, 13, tzinfo=timezone.utc),
            )
            self.assertEqual(report["status"], "state_unreadable")
            self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
