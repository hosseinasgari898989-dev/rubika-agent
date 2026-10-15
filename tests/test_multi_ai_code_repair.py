from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain import multi_ai_code_repair as multi


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
