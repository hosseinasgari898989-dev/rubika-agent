from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain import multi_ai_code_repair as multi
from agent.brain.ai_provider_clients import ProviderResult


class MultiAIRepairPreflightTests(unittest.TestCase):
    def test_missing_provider_secret_stops_before_any_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failure_log = root / "failure.log"
            failure_log.write_text("ERROR: test failed", encoding="utf-8")
            report_path = root / "report.json"
            patch_path = root / "proposal.diff"
            environment = {
                "CODE_AI_DIAGNOSER_PROVIDERS": "groq,mistral,openrouter",
                "CODE_AI_PROPOSER_PROVIDER": "gemini",
                "CODE_AI_REVIEWER_PROVIDERS": "mistral,openrouter",
                "GROQ_API_KEY": "",
                "GEMINI_API_KEY": "g" * 32,
                "MISTRAL_API_KEY": "m" * 32,
                "OPENROUTER_API_KEY": "a" * 32,
            }
            with patch.dict(os.environ, environment, clear=True), patch.object(
                multi, "call_provider_with_ledger"
            ) as call:
                code = multi.main([
                    "--repo-root", str(root),
                    "--failure-log", str(failure_log),
                    "--report-path", str(report_path),
                    "--patch-path", str(patch_path),
                ])
            self.assertEqual(code, 0)
            call.assert_not_called()
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "configuration_missing")
            self.assertFalse(report["request_sent"])
            self.assertFalse(patch_path.exists())

    def test_duplicate_role_providers_stop_before_secret_check_or_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failure_log = root / "failure.log"
            failure_log.write_text("ERROR: test failed", encoding="utf-8")
            report_path = root / "report.json"
            environment = {
                "CODE_AI_DIAGNOSER_PROVIDERS": "groq,groq",
                "CODE_AI_PROPOSER_PROVIDER": "gemini",
                "CODE_AI_REVIEWER_PROVIDERS": "mistral,openrouter",
            }
            with patch.dict(os.environ, environment, clear=True), patch.object(
                multi, "provider_config"
            ) as configure, patch.object(multi, "call_provider_with_ledger") as call:
                multi.main([
                    "--repo-root", str(root),
                    "--failure-log", str(failure_log),
                    "--report-path", str(report_path),
                ])
            configure.assert_not_called()
            call.assert_not_called()
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["error_code"], "provider_list_invalid")
            self.assertFalse(report["request_sent"])


if __name__ == "__main__":
    unittest.main()


class MultiAIEnsembleFlowTests(unittest.TestCase):
    def _run_flow(self, review_decisions):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failure_log = root / "failure.log"
            failure_log.write_text("ERROR: src/app.py test failed", encoding="utf-8")
            report_path = root / "report.json"
            patch_path = root / "proposal.diff"
            calls = []
            proposal_patch = (
                "diff --git a/src/app.py b/src/app.py\n"
                "--- a/src/app.py\n"
                "+++ b/src/app.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            )
            proposal_json = json.dumps({
                "summary": "Minimal repair",
                "root_cause": "A synthetic fixture failure",
                "patch": proposal_patch,
                "confidence": "high",
                "manual_review": False,
            })

            def fake_call(*, provider, role, prompt, on_reserved=None, **kwargs):
                calls.append((provider, role))
                if on_reserved is not None:
                    on_reserved()
                if role == "diagnose":
                    return ProviderResult(provider, provider + "-test-model", provider + " diagnosis", {})
                if role == "propose_patch":
                    return ProviderResult(provider, "gemini-test-model", proposal_json, {})
                decision = review_decisions.get(provider, "approve")
                review_json = json.dumps({
                    "decision": decision,
                    "blocking_reasons": "" if decision == "approve" else "Synthetic safety concern",
                    "manual_review": decision != "approve",
                })
                return ProviderResult(provider, provider + "-test-model", review_json, {})

            environment = {
                "CODE_AI_DIAGNOSER_PROVIDERS": "groq,mistral,openrouter",
                "CODE_AI_PROPOSER_PROVIDER": "gemini",
                "CODE_AI_REVIEWER_PROVIDERS": "mistral,openrouter",
                "GEMINI_API_KEY": "g" * 32,
                "GROQ_API_KEY": "r" * 32,
                "MISTRAL_API_KEY": "m" * 32,
                "OPENROUTER_API_KEY": "o" * 32,
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(
                    multi, "provider_config",
                    side_effect=lambda provider: type(
                        "FakeConfig", (), {"provider": provider, "model": provider + "-test-model"}
                    )(),
                ),
                patch.object(multi, "call_provider_with_ledger", side_effect=fake_call),
                patch.object(multi.gemini, "sanitize_failure_log", return_value="sanitized failure"),
                patch.object(multi.gemini, "extract_candidate_paths", return_value=["src/app.py"]),
                patch.object(multi.gemini, "extract_readonly_test_paths", return_value=[]),
                patch.object(multi.gemini, "collect_readonly_test_context", return_value="read-only test context"),
                patch.object(multi.gemini, "collect_code_context", return_value="source context"),
                patch.object(multi.gemini, "build_prompt", return_value="constrained patch prompt"),
                patch.object(multi.gemini, "validate_patch", return_value=["src/app.py"]),
            ):
                code = multi.main([
                    "--repo-root", str(root),
                    "--failure-log", str(failure_log),
                    "--report-path", str(report_path),
                    "--patch-path", str(patch_path),
                ])

            report = json.loads(report_path.read_text(encoding="utf-8"))
            return code, report, calls, patch_path.exists()

    def test_three_diagnoses_one_proposer_and_two_reviewers_are_required(self):
        code, report, calls, patch_exists = self._run_flow({})
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "patch_ready")
        self.assertTrue(report["review"]["unanimous"])
        self.assertEqual(report["diagnostics_used"], 3)
        self.assertEqual(report["reviewers_approved"], 2)
        self.assertTrue(patch_exists)
        self.assertEqual(len(calls), 6)
        self.assertCountEqual(
            calls,
            [
                ("groq", "diagnose"),
                ("mistral", "diagnose"),
                ("openrouter", "diagnose"),
                ("gemini", "propose_patch"),
                ("mistral", "review_patch"),
                ("openrouter", "review_patch"),
            ],
        )

    def test_one_reviewer_veto_blocks_patch_promotion(self):
        code, report, calls, patch_exists = self._run_flow({"mistral": "reject"})
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "human_review_required")
        self.assertEqual(report["review"]["decision"], "reject")
        self.assertFalse(patch_exists)
        self.assertNotIn(("openrouter", "review_patch"), calls)
