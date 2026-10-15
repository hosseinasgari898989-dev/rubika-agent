from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from termux.collection_gate import decide_collection


NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


class CollectionGateTests(unittest.TestCase):
    def _write_state(self, folder: str, completed_at: str, *, schema_version: int = 1) -> Path:
        path = Path(folder) / "termux_collection_state.json"
        path.write_text(json.dumps({
            "schema_version": schema_version,
            "completed_at": completed_at,
        }), encoding="utf-8")
        return path

    def test_skips_until_five_hours_have_elapsed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._write_state(folder, "2026-10-15T08:00:01+00:00")
            result = decide_collection(event_name="schedule", state_path=path, now=NOW)

        self.assertFalse(result["should_run"])
        self.assertEqual(result["reason"], "cooldown_active")
        self.assertEqual(result["elapsed_seconds"], 4 * 3600 - 1)

    def test_runs_at_exact_five_hour_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._write_state(folder, "2026-10-15T07:00:00Z")
            result = decide_collection(event_name="schedule", state_path=path, now=NOW)

        self.assertTrue(result["should_run"])
        self.assertEqual(result["reason"], "interval_elapsed")
        self.assertEqual(result["elapsed_seconds"], 5 * 3600)

    def test_missing_or_corrupt_state_fails_open(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing.json"
            decision_missing = decide_collection(event_name="schedule", state_path=missing, now=NOW)
            corrupt = Path(folder) / "corrupt.json"
            corrupt.write_text("{not json", encoding="utf-8")
            decision_corrupt = decide_collection(event_name="schedule", state_path=corrupt, now=NOW)

        self.assertTrue(decision_missing["should_run"])
        self.assertTrue(decision_corrupt["should_run"])
        self.assertEqual(decision_missing["reason"], "missing_or_invalid_state_fail_open")
        self.assertEqual(decision_corrupt["reason"], "missing_or_invalid_state_fail_open")

    def test_state_without_timezone_fails_open(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._write_state(folder, "2026-10-15T07:00:00")
            result = decide_collection(event_name="schedule", state_path=path, now=NOW)

        self.assertTrue(result["should_run"])
        self.assertEqual(result["reason"], "missing_or_invalid_state_fail_open")

    def test_future_state_fails_open_instead_of_creating_a_dead_end(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._write_state(folder, "2026-10-15T12:30:00Z")
            result = decide_collection(event_name="schedule", state_path=path, now=NOW)

        self.assertTrue(result["should_run"])
        self.assertEqual(result["reason"], "future_state_timestamp_fail_open")
        self.assertEqual(result["elapsed_seconds"], -1800)

    def test_manual_dispatch_bypasses_the_cooldown(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._write_state(folder, "2026-10-15T11:59:00Z")
            result = decide_collection(event_name="workflow_dispatch", state_path=path, now=NOW)

        self.assertTrue(result["should_run"])
        self.assertEqual(result["reason"], "non_schedule_event_forced")

    def test_invalid_state_schema_fails_open(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self._write_state(folder, "2026-10-15T07:00:00Z", schema_version=2)
            result = decide_collection(event_name="schedule", state_path=path, now=NOW)

        self.assertTrue(result["should_run"])
        self.assertEqual(result["reason"], "missing_or_invalid_state_fail_open")


if __name__ == "__main__":
    unittest.main()
