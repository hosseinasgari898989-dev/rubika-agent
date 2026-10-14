"""Fail-open five-hour gate for metadata collection.

Missing/corrupt state never blocks discovery. Scheduled checks run when the
last successful queue intake is at least the configured interval old; manual
dispatches are explicit overrides. This module only decides whether to collect
metadata and has no download or publishing capability.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_STATE_PATH = Path("artifacts/termux_collection_state.json")
MINIMUM_INTERVAL = timedelta(hours=5)


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid_timestamp")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_missing_timezone")
    return parsed.astimezone(timezone.utc)


def decide_collection(
    *,
    event_name: str,
    state_path: Path = DEFAULT_STATE_PATH,
    now: datetime | None = None,
    minimum_interval: timedelta = MINIMUM_INTERVAL,
) -> dict[str, Any]:
    """Return a fail-open, deterministic due/not-due decision for a workflow tick."""
    if event_name != "schedule":
        return {
            "should_run": True,
            "reason": "non_schedule_event_forced",
            "elapsed_seconds": None,
        }
    if minimum_interval < timedelta(0):
        raise ValueError("minimum_interval_must_not_be_negative")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now_must_have_timezone")
    current = current.astimezone(timezone.utc)

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError("invalid_state_schema")
        completed = _parse_datetime(state.get("completed_at"))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {
            "should_run": True,
            "reason": "missing_or_invalid_state_fail_open",
            "elapsed_seconds": None,
        }

    elapsed_seconds = (current - completed).total_seconds()
    if elapsed_seconds < 0:
        return {
            "should_run": True,
            "reason": "future_state_timestamp_fail_open",
            "elapsed_seconds": elapsed_seconds,
        }
    if elapsed_seconds < minimum_interval.total_seconds():
        return {
            "should_run": False,
            "reason": "cooldown_active",
            "elapsed_seconds": elapsed_seconds,
        }
    return {
        "should_run": True,
        "reason": "interval_elapsed",
        "elapsed_seconds": elapsed_seconds,
    }


def _append_line(path_value: str | None, line: str) -> None:
    if not path_value:
        return
    path = Path(path_value)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line.rstrip("\n") + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--event-name", default=os.getenv("GITHUB_EVENT_NAME", "schedule"))
    parser.add_argument("--github-output", default=os.getenv("GITHUB_OUTPUT"))
    parser.add_argument("--summary", default=os.getenv("GITHUB_STEP_SUMMARY"))
    args = parser.parse_args()

    decision = decide_collection(event_name=args.event_name, state_path=args.state)
    should_run = "true" if decision["should_run"] else "false"
    if args.github_output:
        _append_line(args.github_output, f"should_run={should_run}")
        _append_line(args.github_output, f"gate_reason={decision['reason']}")

    elapsed = decision["elapsed_seconds"]
    if elapsed is None:
        elapsed_text = "not available"
    else:
        elapsed_text = f"{elapsed:.0f} seconds"
    message = (
        f"Collection gate: should_run={should_run}; "
        f"reason={decision['reason']}; elapsed={elapsed_text}."
    )
    print(message)
    if not decision["should_run"] and args.summary:
        _append_line(args.summary, message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
