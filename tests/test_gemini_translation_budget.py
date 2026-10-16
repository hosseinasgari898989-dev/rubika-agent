from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0008_gemini_translation_usage.sql"
DAILY_LIMIT = 10
RESERVE_SQL = (
    "INSERT INTO gemini_translation_usage_daily (usage_day, request_count) VALUES (?, 1) "
    "ON CONFLICT(usage_day) DO UPDATE SET request_count = request_count + 1, "
    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
    "WHERE request_count < ? RETURNING request_count"
)


class GeminiTranslationBudgetTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(MIGRATION.read_text(encoding="utf-8"))

    def tearDown(self):
        self.db.close()

    def test_atomic_reservations_stop_at_ten_per_utc_day(self):
        day = "2026-10-16"
        for expected in range(1, DAILY_LIMIT + 1):
            row = self.db.execute(RESERVE_SQL, (day, DAILY_LIMIT)).fetchone()
            self.assertEqual(row[0], expected)

        self.assertIsNone(self.db.execute(RESERVE_SQL, (day, DAILY_LIMIT)).fetchone())
        stored = self.db.execute(
            "SELECT request_count FROM gemini_translation_usage_daily WHERE usage_day = ?",
            (day,),
        ).fetchone()
        self.assertEqual(stored[0], DAILY_LIMIT)

    def test_next_day_has_an_independent_budget(self):
        for _ in range(DAILY_LIMIT):
            self.db.execute(RESERVE_SQL, ("2026-10-16", DAILY_LIMIT)).fetchone()

        row = self.db.execute(RESERVE_SQL, ("2026-10-17", DAILY_LIMIT)).fetchone()
        self.assertEqual(row[0], 1)

    def test_schema_rejects_counts_above_hard_cap(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO gemini_translation_usage_daily (usage_day, request_count) VALUES (?, ?)",
                ("2026-10-16", DAILY_LIMIT + 1),
            )


if __name__ == "__main__":
    unittest.main()
