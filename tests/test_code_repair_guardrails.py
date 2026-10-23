from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain import code_repair_guardrails as guardrails


VALID_PATCH = """diff --git a/agent/discovery/tavily.py b/agent/discovery/tavily.py
index 1111111..2222222 100644
--- a/agent/discovery/tavily.py
+++ b/agent/discovery/tavily.py
@@ -1,1 +1,1 @@
-def old():
+def new():
"""


class CodeRepairGuardrailsTests(unittest.TestCase):
    def test_exact_allowlist_excludes_publishing_and_security_paths(self):
        self.assertTrue(guardrails.is_allowed_repair_path("agent/discovery/tavily.py"))
        for path in (
            "../agent/discovery/tavily.py",
            "/agent/discovery/tavily.py",
            "src/index.js",
            ".github/workflows/ci.yml",
            "agent/brain/rubika_publisher.py",
            "agent/brain/queue_worker.py",
            "tests/test_queue_worker.py",
        ):
            with self.subTest(path=path):
                self.assertFalse(guardrails.is_allowed_repair_path(path))

    def test_stack_trace_extracts_only_existing_allowlisted_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "agent/discovery").mkdir(parents=True)
            (root / "agent/brain").mkdir(parents=True)
            (root / "agent/discovery/tavily.py").write_text("def f(): pass\n", encoding="utf-8")
            (root / "agent/brain/queue_worker.py").write_text("def f(): pass\n", encoding="utf-8")
            log = (
                f'  File "{root}/agent/discovery/tavily.py", line 10, in f\n'
                f'  File "{root}/agent/brain/queue_worker.py", line 12, in run\n'
            )
            self.assertEqual(
                guardrails.extract_candidate_paths(log, root),
                ["agent/discovery/tavily.py"],
            )

    def test_failure_summary_is_bounded_and_redacts_known_secret(self):
        raw = (
            "Traceback (most recent call last):\n"
            '  File "/runner/rubika-agent/agent/discovery/tavily.py", line 2, in f\n'
            "AssertionError: expected valid result\n"
            "extra-private-secret\n"
        )
        result = guardrails.sanitize_failure_log(raw, secrets=("extra-private-secret",))
        self.assertIn("Traceback", result)
        self.assertIn("AssertionError", result)
        self.assertNotIn("extra-private-secret", result)
        self.assertLessEqual(len(result), guardrails.MAX_LOG_CHARS)

    def test_source_context_redacts_literal_key_like_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "agent/discovery/tavily.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                'API_KEY = "local-test-secret-value"\n'
                'CONFIG = {"api_key": "another-private-secret"}\n'
                'def search():\n    return "https://example.invalid/search?token=abc123"\n',
                encoding="utf-8",
            )
            context = guardrails.collect_code_context(root, ["agent/discovery/tavily.py"])
        self.assertNotIn("local-test-secret-value", context)
        self.assertNotIn("another-private-secret", context)
        self.assertNotIn("?token=abc123", context)
        self.assertIn("agent/discovery/tavily.py", context)

    def test_valid_patch_must_touch_selected_existing_allowlisted_file(self):
        self.assertEqual(
            guardrails.validate_patch(VALID_PATCH, ["agent/discovery/tavily.py"]),
            ["agent/discovery/tavily.py"],
        )

    def test_patch_to_worker_workflow_or_unselected_file_is_rejected(self):
        for path in ("src/index.js", ".github/workflows/ci.yml", "agent/discovery/v7.py"):
            patch = VALID_PATCH.replace(
                "agent/discovery/tavily.py", path
            )
            with self.subTest(path=path), self.assertRaises(guardrails.PatchValidationError):
                guardrails.validate_patch(patch, ["agent/discovery/tavily.py"])

    def test_patch_cannot_create_or_delete_files(self):
        patch = VALID_PATCH.replace(
            "--- a/agent/discovery/tavily.py\n+++ b/agent/discovery/tavily.py",
            "--- /dev/null\n+++ b/agent/discovery/tavily.py",
        )
        with self.assertRaises(guardrails.PatchValidationError):
            guardrails.validate_patch(patch, ["agent/discovery/tavily.py"])

    def test_repair_output_schema_is_strict(self):
        result = guardrails.parse_model_json(json.dumps({
            "summary": "A small fix.",
            "root_cause": "Incorrect condition.",
            "patch": "",
            "confidence": "low",
            "manual_review": True,
        }))
        self.assertEqual(result["summary"], "A small fix.")
        with self.assertRaises(guardrails.CodeRepairError):
            guardrails.parse_model_json(json.dumps({
                "summary": "x",
                "root_cause": "y",
                "patch": "",
                "confidence": "high",
                "manual_review": False,
                "shell_command": "echo unsafe",
            }))

    def test_failed_unittest_module_selects_source_and_readonly_test_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "agent/discovery/tavily.py"
            test_file = root / "tests/test_tavily_discovery.py"
            source.parent.mkdir(parents=True)
            test_file.parent.mkdir(parents=True)
            source.write_text("def search(): return []\n", encoding="utf-8")
            test_file.write_text("class TestSearch: pass\n", encoding="utf-8")
            log = (
                "FAIL: test_search_returns_items "
                "(test_tavily_discovery.TavilyDiscoveryTests.test_search_returns_items)\n"
            )
            self.assertEqual(
                guardrails.extract_candidate_paths(log, root),
                ["agent/discovery/tavily.py"],
            )
            readonly = guardrails.extract_readonly_test_paths(log, root)
            self.assertEqual(readonly, ["tests/test_tavily_discovery.py"])
            context = guardrails.collect_readonly_test_context(root, readonly)
            self.assertIn("READ-ONLY TEST FILE", context)
            with self.assertRaises(guardrails.PatchValidationError):
                guardrails.validate_patch(VALID_PATCH.replace(
                    "agent/discovery/tavily.py", "tests/test_tavily_discovery.py"
                ), ["agent/discovery/tavily.py"])

    def test_fenced_json_response_is_parsed(self):
        payload = {
            "summary": "No safe patch.",
            "root_cause": "The context is insufficient.",
            "patch": "",
            "confidence": "low",
            "manual_review": True,
        }
        fence = chr(96) * 3
        result = guardrails.parse_model_json(f"{fence}json\n{json.dumps(payload)}\n{fence}")
        self.assertEqual(result, payload)

if __name__ == "__main__":
    unittest.main()
