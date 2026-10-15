from __future__ import annotations

import unittest

from scripts.gemini_budget import evaluate_daily_budget


class GeminiBudgetTests(unittest.TestCase):
    def test_only_failed_pushes_to_main_consume_the_conservative_budget(self):
        payload = {
            "total_count": 4,
            "workflow_runs": [
                {"conclusion": "failure", "event": "push", "head_branch": "main"},
                {"conclusion": "success", "event": "push", "head_branch": "main"},
                {"conclusion": "failure", "event": "pull_request", "head_branch": "main"},
                {"conclusion": "failure", "event": "push", "head_branch": "feature/demo"},
            ],
        }
        result = evaluate_daily_budget(payload)
        self.assertTrue(result["allow"])
        self.assertEqual(result["eligible_failed_main_pushes"], 1)
        self.assertEqual(result["reason"], "within_limit")

    def test_fifth_eligible_failure_is_allowed_but_sixth_is_blocked(self):
        def payload(count: int) -> dict:
            runs = [
                {"conclusion": "failure", "event": "push", "head_branch": "main"}
                for _ in range(count)
            ]
            return {"total_count": len(runs), "workflow_runs": runs}

        self.assertTrue(evaluate_daily_budget(payload(5))["allow"])
        sixth = evaluate_daily_budget(payload(6))
        self.assertFalse(sixth["allow"])
        self.assertEqual(sixth["eligible_failed_main_pushes"], 6)
        self.assertEqual(sixth["reason"], "daily_limit_reached")

    def test_incomplete_paginated_history_fails_closed(self):
        payload = {
            "total_count": 101,
            "workflow_runs": [
                {"conclusion": "success", "event": "push", "head_branch": "main"}
                for _ in range(100)
            ],
        }
        result = evaluate_daily_budget(payload)
        self.assertFalse(result["allow"])
        self.assertEqual(result["reason"], "daily_history_incomplete_fail_closed")

    def test_mismatched_record_count_fails_closed(self):
        result = evaluate_daily_budget({
            "total_count": 2,
            "workflow_runs": [
                {"conclusion": "failure", "event": "push", "head_branch": "main"}
            ],
        })
        self.assertFalse(result["allow"])
        self.assertEqual(result["reason"], "daily_history_incomplete_fail_closed")

    def test_unknown_branch_on_failed_push_fails_closed(self):
        result = evaluate_daily_budget({
            "total_count": 1,
            "workflow_runs": [
                {"conclusion": "failure", "event": "push", "head_branch": None}
            ],
        })
        self.assertFalse(result["allow"])
        self.assertEqual(result["reason"], "budget_input_unverifiable")


if __name__ == "__main__":
    unittest.main()
