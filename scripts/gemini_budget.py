"""Fail-closed daily budget calculation for bounded Gemini repair triggers.

Counts failed pushes to main from the Cloud bootstrap checks workflow. This is a
conservative upper bound on possible model requests, not an exact HTTP-call meter.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def evaluate_daily_budget(
    payload: Any,
    *,
    max_run_records: int = 100,
    max_failed_pushes: int = 5,
) -> dict[str, Any]:
    """Count eligible failed main-push runs; fail closed if history is incomplete."""
    denied = {
        "allow": False,
        "reason": "budget_input_unverifiable",
        "eligible_failed_main_pushes": None,
        "records_considered": 0,
        "max_failed_main_pushes": max_failed_pushes,
    }
    if not isinstance(payload, dict):
        return denied

    total = payload.get("total_count")
    runs = payload.get("workflow_runs")
    if type(total) is not int or total < 0 or not isinstance(runs, list):
        return denied
    if total > max_run_records or len(runs) != total:
        result = dict(denied)
        result["reason"] = "daily_history_incomplete_fail_closed"
        return result

    count = 0
    for run in runs:
        if not isinstance(run, dict):
            return denied
        if not {"conclusion", "event", "head_branch"}.issubset(run):
            return denied
        conclusion, event, branch = (
            run.get("conclusion"), run.get("event"), run.get("head_branch")
        )
        if conclusion is not None and not isinstance(conclusion, str):
            return denied
        if not isinstance(event, str):
            return denied
        if event == "push" and conclusion == "failure":
            # For a failed push we must know the branch or deny the budget.
            if not isinstance(branch, str):
                return denied
            if branch == "main":
                count += 1

    return {
        "allow": count <= max_failed_pushes,
        "reason": "within_limit" if count <= max_failed_pushes else "daily_limit_reached",
        "eligible_failed_main_pushes": count,
        "records_considered": len(runs),
        "max_failed_main_pushes": max_failed_pushes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-run-records", type=int, default=100)
    parser.add_argument("--max-failed-pushes", type=int, default=5)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        result = evaluate_daily_budget(None, max_failed_pushes=args.max_failed_pushes)
    else:
        result = evaluate_daily_budget(
            payload,
            max_run_records=args.max_run_records,
            max_failed_pushes=args.max_failed_pushes,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
