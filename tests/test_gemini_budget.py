from __future__ import annotations

import unittest
from pathlib import Path

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

    def test_production_workflow_runs_budget_guard_before_gemini(self):
        repo_root = Path(__file__).resolve().parents[1]
        workflow = (repo_root / ".github/workflows/gemini-code-assistant.yml").read_text(
            encoding="utf-8"
        )

        budget_position = workflow.index("python scripts/gemini_budget.py")
        model_position = workflow.index("Ask Gemini for one bounded repair proposal")
        self.assertLess(budget_position, model_position)
        self.assertIn("--max-run-records 100 --max-failed-pushes 5", workflow)
        self.assertIn('if [[ "$budget_allow" != "true" ]]; then', workflow)
        self.assertIn('echo "should_run=false" >> "$GITHUB_OUTPUT"', workflow)
        self.assertIn("daily repair guard", workflow)

    def test_production_workflow_disallows_workflow_and_upstream_retries(self):
        repo_root = Path(__file__).resolve().parents[1]
        workflow = (repo_root / ".github/workflows/gemini-code-assistant.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("github.run_attempt == 1", workflow)
        self.assertIn("github.event.workflow_run.run_attempt == 1", workflow)


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
