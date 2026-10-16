"""Retired code-patching entry point.

Gemini is permitted to explain incidents only. This module deliberately makes no
provider requests and never proposes, validates, writes, or applies a patch.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import gemini_code_agent as gemini


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--failure-log", type=Path)
    parser.add_argument("--report-path", type=Path, default=Path("multi-ai-code-proposal.json"))
    parser.add_argument("--patch-path", type=Path, default=Path("multi-ai-code-proposal.diff"))
    parser.add_argument("--validate-working-tree", action="store_true")
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()

    if args.validate_working_tree:
        ok, status = gemini.validate_working_tree(root)
        print(json.dumps({"ok": ok, "status": status, "patching_enabled": False}, sort_keys=True))
        return 0 if ok else 2

    report = {
        "ok": True,
        "status": "repair_disabled_gemini_diagnostic_only",
        "request_sent": False,
        "patch_ready": False,
        "patch_applied": False,
        "selected_files": [],
        "error_code": "code_repair_disabled",
        "note": "Use agent.brain.error_supervisor --gemini-diagnosis for advisory diagnosis only.",
    }
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    gemini._write_json(args.report_path, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
