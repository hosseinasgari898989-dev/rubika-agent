"""Offline end-to-end rehearsal of the bounded repair path.

The engine entry point and patch validator are real. Gemini/network calls are
mocked, all git operations happen in a temporary repository, and no GitHub PR,
Worker deployment, database mutation, or Rubika post is performed.
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

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _REPO_ROOT / "agent/brain/gemini_code_agent.py"
_SPEC = importlib.util.spec_from_file_location("gemini_code_agent_e2e_target", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("Could not load the production Gemini repair engine")
gemini = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gemini)

VALID_PATCH = """diff --git a/agent/discovery/tavily.py b/agent/discovery/tavily.py
--- a/agent/discovery/tavily.py
+++ b/agent/discovery/tavily.py
@@ -1,2 +1,2 @@
 def search():
-    return []
+    return ["e2e-fixed"]
"""

PROTECTED_PATCH = VALID_PATCH.replace(
    "agent/discovery/tavily.py", ".github/workflows/ci.yml"
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _sandbox(parent: Path) -> tuple[Path, Path, Path, Path]:
    """Build a clean, disposable repo with a synthetic traceback."""
    root = parent / "sandbox-repo"
    target = root / "agent/discovery/tavily.py"
    target.parent.mkdir(parents=True)
    target.write_text("def search():\n    return []\n", encoding="utf-8")

    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Rubika-Agent E2E Harness")
    _git(root, "config", "user.email", "rubika-agent-e2e@example.invalid")
    _git(root, "add", "agent/discovery/tavily.py")
    _git(root, "commit", "-m", "Create disposable E2E fixture")

    failure_log = parent / "synthetic-failure.log"
    failure_log.write_text(
        "Traceback (most recent call last):\n"
        f'  File "{target}", line 2, in search\n'
        "AssertionError: expected e2e-fixed output\n",
        encoding="utf-8",
    )
    return root, failure_log, parent / "report.json", parent / "proposal.diff"


def _run_engine(
    root: Path,
    failure_log: Path,
    report_path: Path,
    patch_path: Path,
    *,
    proposal_patch: str = VALID_PATCH,
    model_error: Exception | None = None,
) -> dict:
    proposal = {
        "summary": "Synthetic E2E repair.",
        "root_cause": "Fixture returned an empty result.",
        "patch": proposal_patch,
        "confidence": "high",
        "manual_review": False,
    }
    mocked_model = patch.object(
        gemini,
        "call_gemini",
        side_effect=model_error if model_error is not None else None,
        return_value=None if model_error is not None else (proposal, {"totalTokenCount": 9}),
    )
    with (
        patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "synthetic-only-key-never-sent",
                "GEMINI_MODEL": gemini.DEFAULT_MODEL,
            },
        ),
        mocked_model as model_call,
        patch("urllib.request.urlopen") as http_call,
    ):
        exit_code = gemini.main([
            "--repo-root", str(root),
            "--failure-log", str(failure_log),
            "--report-path", str(report_path),
            "--patch-path", str(patch_path),
        ])
    if exit_code != 0:
        raise AssertionError(f"repair engine returned {exit_code}")
    model_call.assert_called_once()
    http_call.assert_not_called()
    return json.loads(report_path.read_text(encoding="utf-8"))


def _validate_and_commit_sandbox(
    root: Path,
    patch_path: Path,
    *,
    expected_output: list[str],
) -> bool:
    """Mirror apply -> path validation -> fixed test -> local review-candidate commit."""
    if not patch_path.is_file() or not patch_path.read_text(encoding="utf-8").strip():
        return False

    try:
        _git(root, "apply", "--check", str(patch_path))
        _git(root, "apply", str(patch_path))
        allowed, _reason = gemini.validate_working_tree(root)
        if not allowed:
            return False

        namespace = runpy.run_path(str(root / "agent/discovery/tavily.py"))
        if namespace["search"]() != expected_output:
            return False

        _git(root, "diff", "--check")
        changed = _git(root, "diff", "--name-only").stdout.splitlines()
        if changed != ["agent/discovery/tavily.py"]:
            return False

        # This commit exists only in a throwaway local repository; nothing is pushed.
        _git(root, "add", "agent/discovery/tavily.py")
        _git(root, "commit", "-m", "Synthetic validated repair candidate")
        committed_files = _git(root, "diff", "--name-only", "HEAD^", "HEAD").stdout.splitlines()
        return committed_files == ["agent/discovery/tavily.py"] and not _git(
            root, "status", "--porcelain"
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError, KeyError, SyntaxError, ValueError):
        return False


def _discard_sandbox_changes(root: Path) -> None:
    _git(root, "reset", "--hard", "HEAD")
    _git(root, "clean", "-fd")


class GeminiRepairE2ETests(unittest.TestCase):
    def test_valid_synthetic_failure_reaches_local_review_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root, log, report_path, patch_path = _sandbox(Path(directory))
            original_head = _git(root, "rev-parse", "HEAD").stdout.strip()
            report = _run_engine(root, log, report_path, patch_path)

            self.assertEqual(report["status"], "patch_ready")
            self.assertEqual(report["patch_files"], ["agent/discovery/tavily.py"])
            self.assertTrue(_validate_and_commit_sandbox(
                root, patch_path, expected_output=["e2e-fixed"]
            ))
            self.assertNotEqual(_git(root, "rev-parse", "HEAD").stdout.strip(), original_head)
            self.assertEqual(
                _git(root, "diff", "--name-only", "HEAD^", "HEAD").stdout.splitlines(),
                ["agent/discovery/tavily.py"],
            )
            # An external PR is intentionally not created by this test.
            self.assertFalse((Path(directory) / "github-pr-created.json").exists())

    def test_failed_fixed_check_discards_patch_and_restores_clean_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root, log, report_path, patch_path = _sandbox(Path(directory))
            original_head = _git(root, "rev-parse", "HEAD").stdout.strip()
            report = _run_engine(root, log, report_path, patch_path)
            self.assertEqual(report["status"], "patch_ready")

            self.assertFalse(_validate_and_commit_sandbox(
                root, patch_path, expected_output=["wrong-output"]
            ))
            _discard_sandbox_changes(root)
            self.assertEqual(_git(root, "rev-parse", "HEAD").stdout.strip(), original_head)
            self.assertFalse(_git(root, "status", "--porcelain").stdout.strip())

    def test_protected_path_proposal_never_becomes_a_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root, log, report_path, patch_path = _sandbox(Path(directory))
            original_head = _git(root, "rev-parse", "HEAD").stdout.strip()
            report = _run_engine(
                root, log, report_path, patch_path, proposal_patch=PROTECTED_PATCH
            )
            self.assertEqual(report["status"], "patch_rejected")
            self.assertFalse(patch_path.exists())
            self.assertEqual(_git(root, "rev-parse", "HEAD").stdout.strip(), original_head)
            self.assertFalse(_git(root, "status", "--porcelain").stdout.strip())

    def test_rate_limit_stops_without_patch_or_local_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root, log, report_path, patch_path = _sandbox(Path(directory))
            original_head = _git(root, "rev-parse", "HEAD").stdout.strip()
            report = _run_engine(
                root, log, report_path, patch_path,
                model_error=gemini.GeminiAssistantError("gemini_http_429"),
            )
            self.assertEqual(report["status"], "assistant_unavailable")
            self.assertEqual(report["error_code"], "gemini_http_429")
            self.assertFalse(patch_path.exists())
            self.assertEqual(_git(root, "rev-parse", "HEAD").stdout.strip(), original_head)
            self.assertFalse(_git(root, "status", "--porcelain").stdout.strip())

    def test_production_workflow_gates_model_and_pr_after_failure_and_validation(self):
        workflow = (_REPO_ROOT / ".github/workflows/gemini-code-assistant.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("types: [completed]", workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'failure'", workflow)
        self.assertIn("github.event.workflow_run.event == 'push'", workflow)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", workflow)

        freshness = workflow.index("Check freshness, duplicate proposals and daily request budget")
        model = workflow.index("Ask Gemini for one bounded repair proposal")
        validation = workflow.index("Apply proposal in the disposable runner and run fixed tests")
        promote = workflow.index("Push tested patch to a repair branch and open a review PR")
        self.assertLess(freshness, model)
        self.assertLess(model, validation)
        self.assertLess(validation, promote)
        self.assertIn("if: steps.validation.outputs.tests_passed == 'true'", workflow)
        self.assertIn("git apply --check", workflow)
        self.assertIn("git reset --hard HEAD", workflow)
        self.assertIn("gh pr create", workflow)
        self.assertIn("will not be merged automatically", workflow)


if __name__ == "__main__":
    unittest.main()
