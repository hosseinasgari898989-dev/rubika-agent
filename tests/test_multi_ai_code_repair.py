from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain import multi_ai_code_repair as multi


class RetiredCodeRepairTests(unittest.TestCase):
    def test_entry_point_is_inert_and_does_not_request_or_write_a_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failure_log = root / "failure.log"
            failure_log.write_text("FAIL: test_safe_synthetic_failure", encoding="utf-8")
            report_path = root / "report.json"
            patch_path = root / "proposal.diff"
            with patch.object(multi.gemini, "_write_json", wraps=multi.gemini._write_json) as write_report:
                code = multi.main([
                    "--repo-root", str(root),
                    "--failure-log", str(failure_log),
                    "--report-path", str(report_path),
                    "--patch-path", str(patch_path),
                ])
            self.assertEqual(code, 0)
            write_report.assert_called_once()
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "repair_disabled_gemini_diagnostic_only")
            self.assertFalse(report["request_sent"])
            self.assertFalse(report["patch_ready"])
            self.assertFalse(report["patch_applied"])
            self.assertFalse(patch_path.exists())

    def test_retired_entry_point_does_not_import_active_provider_call_path(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "agent" / "brain" / "multi_ai_code_repair.py").read_text(encoding="utf-8")
        self.assertNotIn("call_provider_with_ledger", source)
        self.assertNotIn("GROQ_API_KEY", source)
        self.assertNotIn("OPENROUTER_API_KEY", source)
        self.assertIn("repair_disabled_gemini_diagnostic_only", source)
        self.assertIn("validate_working_tree", source)

    def test_working_tree_validator_mode_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(multi.gemini, "validate_working_tree", return_value=(True, "clean")) as validate:
                code = multi.main(["--repo-root", directory, "--validate-working-tree"])
        self.assertEqual(code, 0)
        validate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
