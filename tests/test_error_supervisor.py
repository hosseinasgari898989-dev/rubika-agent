from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain.error_supervisor import local_recommendation, recommend_error


class ErrorSupervisorTests(unittest.TestCase):
    def test_uncertain_delivery_always_requires_human_review(self):
        result = local_recommendation(
            event_code="rubika_delivery_uncertain",
            stage="publish",
            error_code="delivery_receipt_missing",
        )
        self.assertEqual(result["recommendation"]["action"], "human_review")
        self.assertTrue(result["recommendation"]["human_review"])
        self.assertEqual(result["recommendation"]["next_step_code"], "review_publisher_delivery_receipt")
        self.assertFalse(result["applied"])

    def test_blocked_source_skips_that_source_without_retry_loop(self):
        result = local_recommendation(
            event_code="source_blocked",
            stage="collector",
            error_code="http_202",
        )
        self.assertEqual(result["recommendation"]["action"], "skip_source_and_continue")
        self.assertEqual(result["recommendation"]["next_step_code"], "continue_with_other_sources")

    def test_transient_network_failure_only_recommends_next_scheduled_retry(self):
        result = local_recommendation(
            event_code="queue_intake_failed",
            stage="queue_intake",
            error_code="control_api_unreachable",
        )
        self.assertEqual(result["recommendation"]["action"], "retry_next_scheduled_run")
        self.assertEqual(result["recommendation"]["next_step_code"], "wait_for_next_schedule")
        self.assertFalse(result["applied"])

    def test_cloud_recommendation_is_passed_through_only_if_bounded(self):
        class FakeClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def supervise_error(self, **kwargs):
                self.last_payload = kwargs
                return {
                    "ok": True,
                    "source": "workers_ai",
                    "recommendation": {
                        "action": "inspect_queue",
                        "reason_code": "queue_duplicate",
                        "next_step_code": "inspect_d1_queue_counts",
                        "human_review": False,
                    },
                    "applied": False,
                    "daily_ai_limit": 4,
                }

        result = recommend_error(
            event_code="queue_read_failed",
            stage="queue_read",
            error_code="control_api_http_503",
            platform="tiktok",
            attempt_count=1,
            counts={"queued": 8},
            client_factory=FakeClient,
        )
        self.assertEqual(result["source"], "workers_ai")
        self.assertEqual(result["recommendation"]["action"], "inspect_queue")
        self.assertFalse(result["applied"])

    def test_prefers_current_local_fallback_over_stale_remote_deterministic_advice(self):
        class StaleWorkerFallback:
            def __init__(self, *_args, **_kwargs):
                pass

            def supervise_error(self, **_kwargs):
                return {
                    "ok": True,
                    "source": "deterministic_fallback",
                    "recommendation": {
                        "action": "human_review",
                        "reason_code": "unknown_error",
                        "next_step_code": "review_error_code_and_recent_logs",
                        "human_review": True,
                    },
                    "applied": False,
                    "daily_ai_limit": 4,
                }

        result = recommend_error(
            event_code="queue_intake_failed",
            stage="queue_intake",
            error_code="control_api_unreachable",
            client_factory=StaleWorkerFallback,
        )
        self.assertEqual(result["source"], "local_fallback")
        self.assertEqual(result["recommendation"]["action"], "retry_next_scheduled_run")
        self.assertEqual(result["recommendation"]["next_step_code"], "wait_for_next_schedule")
        self.assertFalse(result["applied"])

    def test_api_unavailable_uses_safe_local_fallback_without_leaking_error_text(self):
        class BrokenClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def supervise_error(self, **_kwargs):
                raise RuntimeError("LEAKED_TEST_SECRET https://secret.invalid")

        result = recommend_error(
            event_code="rubika_delivery_uncertain",
            stage="publish",
            error_code="delivery_receipt_missing",
            client_factory=BrokenClient,
        )
        rendered = json.dumps(result)
        self.assertEqual(result["source"], "local_fallback")
        self.assertEqual(result["recommendation"]["action"], "human_review")
        self.assertNotIn("LEAKED_TEST_SECRET", rendered)
        self.assertNotIn("https://", rendered)

    def test_rejects_unsafe_event_input_before_network(self):
        with patch("agent.brain.error_supervisor.CloudState") as client:
            result = recommend_error(
                event_code="rubika_delivery_uncertain;token=secret",
                stage="publish",
                error_code="delivery_receipt_missing",
            )
        client.assert_not_called()
        self.assertEqual(result["source"], "local_fallback")
        self.assertEqual(result["recommendation"]["action"], "human_review")

    def test_report_file_contains_only_bounded_recommendation(self):
        from agent.brain.error_supervisor import _write_report

        payload = {
            "ok": True,
            "source": "local_fallback",
            "recommendation": {
                "action": "human_review",
                "reason_code": "unknown_error",
                "next_step_code": "review_error_code_and_recent_logs",
                "human_review": True,
            },
            "applied": False,
            "daily_ai_limit": 4,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supervisor.json"
            _write_report(path, payload)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved, payload)
        self.assertFalse(saved["applied"])


if __name__ == "__main__":
    unittest.main()
