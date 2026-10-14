from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent.brain.five_hour_gate import MIN_INTERVAL, decide


class FiveHourGateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "termux_collection_state.json"
        self.now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    def save_state(self, completed_at: str):
        self.state_path.write_text(json.dumps({
            "schema_version": 1,
            "completed_at": completed_at,
            "inserted_count": 4,
        }), encoding="utf-8")

    def test_schedule_skips_before_five_hours(self):
        self.save_state("2026-10-15T07:00:01Z")
        result = decide(event_name="schedule", state_path=self.state_path, now=self.now)
        self.assertFalse(result["should_run"])
        self.assertEqual(result["reason"], "five_hour_interval_not_elapsed")
        self.assertEqual(result["required_seconds"], 18000)

    def test_schedule_runs_at_exact_five_hour_boundary(self):
        self.save_state("2026-10-15T07:00:00Z")
        result = decide(event_name="schedule", state_path=self.state_path, now=self.now)
        self.assertTrue(result["should_run"])
        self.assertEqual(result["reason"], "five_hour_interval_elapsed")

    def test_manual_dispatch_can_be_used_for_recovery(self):
        self.save_state("2026-10-15T11:59:30Z")
        result = decide(event_name="workflow_dispatch", state_path=self.state_path, now=self.now)
        self.assertTrue(result["should_run"])
        self.assertEqual(result["reason"], "manual_or_non_schedule_trigger")

    def test_missing_or_corrupted_state_fails_open_to_metadata_collection(self):
        result = decide(event_name="schedule", state_path=self.state_path, now=self.now)
        self.assertTrue(result["should_run"])
        self.assertEqual(result["reason"], "no_valid_successful_cycle_marker")
        self.state_path.write_text("{broken", encoding="utf-8")
        result = decide(event_name="schedule", state_path=self.state_path, now=self.now)
        self.assertTrue(result["should_run"])

    def test_future_state_timestamp_fails_open_and_reports_drift(self):
        self.save_state("2026-10-15T12:01:00Z")
        result = decide(event_name="schedule", state_path=self.state_path, now=self.now)
        self.assertTrue(result["should_run"])
        self.assertEqual(result["reason"], "state_timestamp_in_future_fail_open")
        self.assertLess(result["elapsed_seconds"], 0)

    def test_timezone_naive_now_is_rejected(self):
        with self.assertRaises(ValueError):
            decide(
                event_name="schedule",
                state_path=self.state_path,
                now=datetime(2026, 10, 15, 12, 0),
            )

    def test_non_positive_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            decide(
                event_name="schedule",
                state_path=self.state_path,
                now=self.now,
                min_interval=timedelta(0),
            )


if __name__ == "__main__":
    unittest.main()
