import json
import unittest

from scripts.mob_x_preflight import BODY_TIMEOUT_SECONDS, fetch_items


class FakeResponse:
    def __init__(self, body=None, status=200, read_error=None):
        self.status = status
        self.body = body
        self.read_error = read_error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        if self.read_error:
            raise self.read_error
        return json.dumps(self.body).encode("utf-8")


class MobXPreflightRetryTests(unittest.TestCase):
    def test_body_timeout_then_success_retries_with_two_second_backoff(self):
        calls = []
        delays = []
        responses = [
            FakeResponse(read_error=TimeoutError("do not expose this message or token")),
            FakeResponse({"ok": True, "items": [{"type": "mob_x_link", "status": "confirmed"}]}),
        ]

        def opener(request, timeout):
            calls.append((request, timeout))
            return responses.pop(0)

        items, error_detail, attempts = fetch_items(
            "jobs",
            "https://worker.example",
            "/api/console/jobs?type=mob_x_link&status=confirmed&limit=5",
            "unit-test-token-must-not-appear",
            opener=opener,
            sleeper=delays.append,
        )

        self.assertEqual(len(items), 1)
        self.assertIsNone(error_detail)
        self.assertEqual(attempts, 2)
        self.assertEqual(delays, [2])
        self.assertEqual([timeout for _, timeout in calls], [BODY_TIMEOUT_SECONDS] * 2)
        self.assertEqual(calls[0][0].get_header("Authorization"), "Bearer unit-test-token-must-not-appear")
        self.assertIn("type=mob_x_link&status=confirmed&limit=5", calls[0][0].full_url)

    def test_exhausted_timeouts_return_sanitized_error_detail(self):
        delays = []
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return FakeResponse(status=200, read_error=TimeoutError("sensitive token or URL should not leak"))

        items, error_detail, attempts = fetch_items(
            "jobs",
            "https://worker.example",
            "/api/console/jobs?type=mob_x_link&status=confirmed&limit=5",
            "unit-test-token-must-not-appear",
            opener=opener,
            sleeper=delays.append,
        )

        self.assertEqual(items, [])
        self.assertEqual(attempts, 3)
        self.assertEqual(delays, [2, 5])
        self.assertEqual(error_detail, "endpoint=jobs attempts=3 status=200 error=TimeoutError")
        self.assertNotIn("sensitive", error_detail)
        self.assertNotIn("unit-test-token", error_detail)


if __name__ == "__main__":
    unittest.main()
