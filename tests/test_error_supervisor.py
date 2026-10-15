from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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

    def test_recommend_error_uses_local_rules_without_remote_ai(self):
        result = recommend_error(
            event_code="queue_read_failed",
            stage="queue_read",
            error_code="control_api_http_503",
            platform="tiktok",
            attempt_count=1,
            counts={"queued": 8},
        )
        self.assertEqual(result["source"], "local_fallback")
        self.assertEqual(result["recommendation"]["action"], "retry_next_scheduled_run")
        self.assertEqual(result["recommendation"]["next_step_code"], "wait_for_next_schedule")
        self.assertFalse(result["applied"])
        self.assertNotIn("daily_ai_limit", result)

    def test_rejects_unsafe_event_input(self):
        result = recommend_error(
            event_code="invalid-event-extra",
            stage="publish",
            error_code="delivery_receipt_missing",
        )
        self.assertEqual(result["source"], "local_fallback")
        self.assertEqual(result["recommendation"]["action"], "human_review")
        rendered = json.dumps(result)
        self.assertNotIn("invalid-event-extra", rendered)

    def test_worker_has_no_error_supervisor_ai_but_keeps_music_and_translation(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "index.js").read_text(encoding="utf-8")
        self.assertNotIn("AI_SUPERVISOR_MODEL", source)
        self.assertNotIn("/api/brain/supervise-error", source)
        self.assertIn("OFFICIAL_TRANSLATION_MODEL", source)
        self.assertIn("VISION_MODEL", source)
        self.assertIn("translateOfficial(env", source)
        self.assertIn("moderateMusic(env", source)

    def test_report_file_contains_only_bounded_recommendation(self):
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
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supervisor.json"
            from agent.brain.error_supervisor import _write_report
            _write_report(path, payload)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved, payload)
        self.assertFalse(saved["applied"])


if __name__ == "__main__":
    unittest.main()
