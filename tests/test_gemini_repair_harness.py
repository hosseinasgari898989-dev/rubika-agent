"""Offline, deterministic integration tests for the bounded Gemini repair flow.

These tests use a synthetic CI traceback and a mocked model response inside a
temporary Git repository. They never call Gemini, GitHub, Cloudflare, or Rubika.
"""
from __future__ import annotations

import importlib.util
import json
import os
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Load this standard-library-only module directly. Importing agent.brain would
# execute its package initializer and unnecessarily require every runtime dependency.
_MODULE_PATH = Path(__file__).resolve().parents[1] / "agent/brain/gemini_code_agent.py"
_MODULE_SPEC = importlib.util.spec_from_file_location(
    "gemini_code_agent_harness_target", _MODULE_PATH
)
if _MODULE_SPEC is None or _MODULE_SPEC.loader is None:
    raise ImportError("Could not load the Gemini repair engine for the offline harness")
gemini = importlib.util.module_from_spec(_MODULE_SPEC)
_MODULE_SPEC.loader.exec_module(gemini)


REPAIR_PATCH = """diff --git a/agent/discovery/tavily.py b/agent/discovery/tavily.py
--- a/agent/discovery/tavily.py
+++ b/agent/discovery/tavily.py
@@ -1,2 +1,2 @@
 def search():
-    return []
+    return ["synthetic-fixed"]
"""

PROTECTED_PATH_PATCH = REPAIR_PATCH.replace(
    "agent/discovery/tavily.py", ".github/workflows/ci.yml"
)


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _make_sandbox(parent: Path) -> tuple[Path, Path, Path, Path]:
    """Create a minimal disposable repo and synthetic CI failure outside it."""
    root = parent / "sandbox-repo"
    target = root / "agent/discovery/tavily.py"
    target.parent.mkdir(parents=True)
    target.write_text("def search():\n    return []\n", encoding="utf-8")

    _run_git(root, "init", "-q")
    _run_git(root, "config", "user.name", "Gemini Harness Test")
    _run_git(root, "config", "user.email", "gemini-harness@example.invalid")
    _run_git(root, "add", "agent/discovery/tavily.py")
    _run_git(root, "commit", "-m", "Create disposable fixture")

    failure_log = parent / "synthetic-ci-failure.log"
    failure_log.write_text(
        "Traceback (most recent call last):\n"
        f'  File "{target}", line 2, in search\n'
        "AssertionError: expected synthetic-fixed output\n",
        encoding="utf-8",
    )
    return (
        root,
        failure_log,
        parent / "proposal-report.json",
        parent / "proposal.diff",
    )


def _run_mocked_engine(
    root: Path,
    failure_log: Path,
    report_path: Path,
    patch_path: Path,
    proposed_diff: str,
) -> dict:
    """Run the real engine entry point while replacing the network call."""
    proposal = {
        "summary": "Synthetic deterministic repair.",
        "root_cause": "Fixture returns an empty list.",
        "patch": proposed_diff,
        "confidence": "high",
        "manual_review": False,
    }
    with (
        patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "synthetic-only-key-never-sent",
                "GEMINI_MODEL": gemini.DEFAULT_MODEL,
            },
        ),
        patch.object(
            gemini,
            "call_gemini",
            return_value=(proposal, {"totalTokenCount": 7}),
        ) as mocked_model,
        patch("urllib.request.urlopen") as mocked_http,
    ):
        exit_code = gemini.main(
            [
                "--repo-root", str(root),
                "--failure-log", str(failure_log),
                "--report-path", str(report_path),
                "--patch-path", str(patch_path),
            ]
        )
    if exit_code != 0:
        raise AssertionError(f"repair engine returned {exit_code}")
    mocked_model.assert_called_once()
    mocked_http.assert_not_called()
    return json.loads(report_path.read_text(encoding="utf-8"))


def _apply_and_check_sandbox(
    root: Path, patch_path: Path, *, expected_result: list[str]
) -> bool:
    """Apply only inside the disposable repo and run a deterministic fixture check."""
    if not patch_path.is_file() or not patch_path.read_text(encoding="utf-8").strip():
        return False
    try:
        _run_git(root, "apply", "--check", str(patch_path))
        _run_git(root, "apply", str(patch_path))
        ok, _reason = gemini.validate_working_tree(root)
        if not ok:
            return False
        namespace = runpy.run_path(str(root / "agent/discovery/tavily.py"))
        return namespace["search"]() == expected_result
    except (OSError, subprocess.SubprocessError, KeyError, SyntaxError, ValueError):
        return False
    finally:
        # Mirrors the production workflow's discard-on-failure rule in the sandbox.
        _run_git(root, "reset", "--hard", "HEAD")


class GeminiRepairHarnessTests(unittest.TestCase):
    def test_synthetic_failure_reaches_validated_review_ready_state_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root, failure_log, report_path, patch_path = _make_sandbox(parent)
            report = _run_mocked_engine(
                root, failure_log, report_path, patch_path, REPAIR_PATCH
            )

            self.assertEqual(report["status"], "patch_ready")
            self.assertTrue(report["request_sent"])
            self.assertEqual(report["patch_files"], ["agent/discovery/tavily.py"])

            passed = _apply_and_check_sandbox(
                root, patch_path, expected_result=["synthetic-fixed"]
            )
            self.assertTrue(passed, "valid synthetic patch should pass its sandbox check")
            self.assertFalse(_run_git(root, "status", "--porcelain").stdout.strip())
            # This only proves local review readiness; it deliberately opens no real PR.
            self.assertTrue(report["confidence"] == "high" and not report["manual_review"])

    def test_failed_sandbox_check_is_discarded_and_not_review_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root, failure_log, report_path, patch_path = _make_sandbox(parent)
            report = _run_mocked_engine(
                root, failure_log, report_path, patch_path, REPAIR_PATCH
            )
            self.assertEqual(report["status"], "patch_ready")

            passed = _apply_and_check_sandbox(
                root, patch_path, expected_result=["an-intentionally-wrong-expectation"]
            )
            self.assertFalse(passed)
            self.assertFalse(_run_git(root, "status", "--porcelain").stdout.strip())
            self.assertFalse(passed and report["status"] == "patch_ready")

    def test_protected_path_proposal_is_rejected_without_writing_a_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root, failure_log, report_path, patch_path = _make_sandbox(parent)
            report = _run_mocked_engine(
                root, failure_log, report_path, patch_path, PROTECTED_PATH_PATCH
            )
            self.assertEqual(report["status"], "patch_rejected")
            self.assertEqual(report["error_code"], "patch_path_not_allowed")
            self.assertFalse(patch_path.exists())
            self.assertFalse(_run_git(root, "status", "--porcelain").stdout.strip())


if __name__ == "__main__":
    unittest.main()
