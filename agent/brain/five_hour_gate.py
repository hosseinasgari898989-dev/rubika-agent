"""Decision helper for the scheduled five-hour collection gate.

The GitHub workflow may poll more frequently, but metadata collection only runs
when five hours elapsed since a successful D1 intake. Missing/corrupt markers
fail open to a metadata-only collection, not a publish action.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MIN_INTERVAL = timedelta(hours=5)


def decide(
    *,
    event_name: str,
    state_path: Path,
    now: datetime | None = None,
    min_interval: timedelta = MIN_INTERVAL,
) -> dict[str, Any]:
    if min_interval <= timedelta(0):
        raise ValueError("min_interval_must_be_positive")
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now_must_be_timezone_aware")
    now = now.astimezone(timezone.utc)

    if event_name != "schedule":
        return {
            "should_run": True,
            "reason": "manual_or_non_schedule_trigger",
            "elapsed_seconds": None,
            "required_seconds": int(min_interval.total_seconds()),
        }

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError("state_schema_invalid")
        raw = state.get("completed_at")
        if not isinstance(raw, str) or not raw:
            raise ValueError("completion_timestamp_missing")
        completed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if completed.tzinfo is None:
            raise ValueError("completion_timestamp_not_timezone_aware")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {
            "should_run": True,
            "reason": "no_valid_successful_cycle_marker",
            "elapsed_seconds": None,
            "required_seconds": int(min_interval.total_seconds()),
        }

    elapsed_seconds = int((now - completed.astimezone(timezone.utc)).total_seconds())
    if elapsed_seconds < 0:
        return {
            "should_run": True,
            "reason": "state_timestamp_in_future_fail_open",
            "elapsed_seconds": elapsed_seconds,
            "required_seconds": int(min_interval.total_seconds()),
        }
    if elapsed_seconds < int(min_interval.total_seconds()):
        return {
            "should_run": False,
            "reason": "five_hour_interval_not_elapsed",
            "elapsed_seconds": elapsed_seconds,
            "required_seconds": int(min_interval.total_seconds()),
        }
    return {
        "should_run": True,
        "reason": "five_hour_interval_elapsed",
        "elapsed_seconds": elapsed_seconds,
        "required_seconds": int(min_interval.total_seconds()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--state-file", type=Path, default=Path("artifacts/termux_collection_state.json"))
    args = parser.parse_args()
    decision = decide(event_name=args.event_name, state_path=args.state_file)
    print(json.dumps(decision, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
