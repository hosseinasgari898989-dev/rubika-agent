from __future__ import annotations

import json
import unittest

from agent.brain import owner_health


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        body = json.dumps(self.payload).encode("utf-8")
        return body if size < 0 else body[:size]


class FakeOpener:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {"workflow_runs": []}
        self.error = error
        self.urls = []

    def urlopen(self, request, timeout):
        self.urls.append((request.full_url, timeout))
        if self.error is not None:
            raise self.error
        return FakeResponse(self.payload)


class OwnerHealthTests(unittest.TestCase):
    def test_failure_streak_counts_main_scheduled_failures_until_success(self):
        opener = FakeOpener({
            "workflow_runs": [
                {"head_branch": "main", "event": "schedule", "status": "in_progress",
                 "conclusion": None, "run_number": 45},
                {"head_branch": "feature/test", "event": "schedule", "status": "completed",
                 "conclusion": "failure", "run_number": 44},
                {"head_branch": "main", "event": "pull_request", "status": "completed",
                 "conclusion": "failure", "run_number": 43},
                {"head_branch": "main", "event": "schedule", "status": "completed",
                 "conclusion": "failure", "run_number": 42},
                {"head_branch": "main", "event": "schedule", "status": "completed",
                 "conclusion": "skipped", "run_number": 41},
                {"head_branch": "main", "event": "schedule", "status": "completed",
                 "conclusion": "failure", "run_number": 40},
                {"head_branch": "main", "event": "schedule", "status": "completed",
                 "conclusion": "success", "run_number": 39},
                {"head_branch": "main", "event": "schedule", "status": "completed",
                 "conclusion": "failure", "run_number": 38},
            ]
        })
        result = owner_health.fetch_workflow_failure_streak("termux_discovery", opener=opener)
        self.assertTrue(result["available"])
        self.assertEqual(result["consecutive_failures"], 2)
        self.assertEqual(result["last_run_number"], 42)
        self.assertEqual(result["last_conclusion"], "failure")
        self.assertIn("termux-docker-discovery.yml/runs", opener.urls[0][0])
        self.assertEqual(opener.urls[0][1], owner_health.GITHUB_TIMEOUT_SECONDS)

    def test_success_as_latest_completed_run_resets_streak_to_zero(self):
        opener = FakeOpener({
            "workflow_runs": [
                {"head_branch": "main", "event": "schedule", "status": "completed",
                 "conclusion": "success", "run_number": 9},
                {"head_branch": "main", "event": "schedule", "status": "completed",
                 "conclusion": "failure", "run_number": 8},
            ]
        })
        result = owner_health.fetch_workflow_failure_streak("daily_queue", opener=opener)
        self.assertEqual(result["consecutive_failures"], 0)
        self.assertEqual(result["last_run_number"], 9)
        self.assertEqual(result["last_conclusion"], "success")

    def test_deploy_monitor_filters_push_events(self):
        opener = FakeOpener({
            "workflow_runs": [
                {"head_branch": "main", "event": "workflow_dispatch", "status": "completed",
                 "conclusion": "failure", "run_number": 12},
                {"head_branch": "main", "event": "push", "status": "completed",
                 "conclusion": "failure", "run_number": 11},
                {"head_branch": "main", "event": "push", "status": "completed",
                 "conclusion": "success", "run_number": 10},
            ]
        })
        result = owner_health.fetch_workflow_failure_streak("deploy_worker", opener=opener)
        self.assertEqual(result["consecutive_failures"], 1)
        self.assertIn("deploy-worker.yml/runs", opener.urls[0][0])

    def test_unavailable_github_api_is_reported_without_raw_error_details(self):
        opener = FakeOpener(error=OSError("private host and request content"))
        result = owner_health.fetch_workflow_failure_streak("mob_x", opener=opener)
        self.assertFalse(result["available"])
        self.assertIsNone(result["consecutive_failures"])
        self.assertNotIn("private", json.dumps(result))

    def test_daily_usage_reduction_requires_measurable_seventy_percent_counter(self):
        self.assertFalse(owner_health.should_reduce_nonessential_writes(None))
        self.assertFalse(owner_health.should_reduce_nonessential_writes({
            "daily_usage": owner_health.unavailable_daily_usage()
        }))
        self.assertFalse(owner_health.should_reduce_nonessential_writes({
            "daily_usage": {"d1_rows_written": 69_999}
        }))
        self.assertTrue(owner_health.should_reduce_nonessential_writes({
            "daily_usage": {"d1_rows_written": 70_000}
        }))
        self.assertTrue(owner_health.should_reduce_nonessential_writes({
            "daily_usage": {"worker_requests": 70_000}
        }))
        self.assertFalse(owner_health.should_reduce_nonessential_writes({
            "daily_usage": {"d1_rows_written": float("nan")}
        }))

    def test_health_report_marks_static_d1_sample_and_unreadable_services(self):
        snapshot = owner_health.unavailable_health_snapshot()
        snapshot["sampled_at"] = "2026-11-03T12:00:00+00:00"
        snapshot["workflows"]["termux_discovery"] = {
            "available": True, "consecutive_failures": 2,
            "last_run_number": 110, "last_conclusion": "failure",
        }
        lines = owner_health.render_health_section(snapshot)
        rendered = "\n".join(lines)
        self.assertIn("2026-11-03", rendered)
        self.assertIn("اندازهٔ زنده در digest قابل خواندن نیست", rendered)
        self.assertIn("50 MB", rendered)
        self.assertIn("250 MB", rendered)
        self.assertIn("MEGA", rendered)
        self.assertIn("قابل خواندن نیست", rendered)
        self.assertIn("2 شکست متوالی", rendered)
        self.assertIn("مصرف روزانه", rendered)
        self.assertIn("کاهش بار خودکار فعال نیست", rendered)
        self.assertEqual(sum("MEGA:" in line for line in lines), 1)


if __name__ == "__main__":
    unittest.main()
