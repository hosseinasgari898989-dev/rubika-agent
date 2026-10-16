from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class D1MigrationTests(unittest.TestCase):
    def test_initial_schema_and_brain_metadata_migration_apply_cleanly(self):
        db = sqlite3.connect(":memory:")
        try:
            db.executescript((ROOT / "migrations" / "0001_init.sql").read_text(encoding="utf-8"))
            db.executescript((ROOT / "migrations" / "0002_brain_metadata.sql").read_text(encoding="utf-8"))
            db.executescript((ROOT / "migrations" / "0003_ai_usage_daily.sql").read_text(encoding="utf-8"))
            db.executescript((ROOT / "migrations" / "0004_automation_queue.sql").read_text(encoding="utf-8"))
            db.executescript((ROOT / "migrations" / "0005_owner_daily_digest.sql").read_text(encoding="utf-8"))

            columns = {
                row[1] for row in db.execute("PRAGMA table_info(items)").fetchall()
            }
            self.assertTrue({
                "media_type", "source_tier", "storage_key", "validation_json",
                "moderation_json", "review_state", "original_caption", "persian_translation",
            }.issubset(columns))

            tables = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertTrue({
                "agent_config", "sources", "items", "runs", "delivery_attempts", "brain_events",
                "ai_usage_daily", "automation_queue"
            }.issubset(tables))

            db.execute(
                "INSERT INTO items (id, platform, source_key, external_id, canonical_url, status, media_type, source_tier, review_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "tiktok:7616247520657771796", "tiktok", "@creator",
                    "7616247520657771796",
                    "https://www.tiktok.com/@creator/video/7616247520657771796",
                    "discovered", "video", "unknown", "pending",
                ),
            )
            row = db.execute(
                "SELECT media_type, source_tier, review_state FROM items WHERE id = ?",
                ("tiktok:7616247520657771796",),
            ).fetchone()
            self.assertEqual(row, ("video", "unknown", "pending"))
            db.execute("INSERT INTO ai_usage_daily (usage_day, request_count) VALUES ('2026-10-13', 1)")
            self.assertEqual(
                db.execute("SELECT request_count FROM ai_usage_daily WHERE usage_day = '2026-10-13'").fetchone(),
                (1,),
            )
        finally:
            db.close()

    def test_persistent_queue_constraints_and_duplicate_identity(self):
        db = sqlite3.connect(":memory:")
        try:
            db.executescript((ROOT / "migrations" / "0001_init.sql").read_text(encoding="utf-8"))
            db.executescript((ROOT / "migrations" / "0002_brain_metadata.sql").read_text(encoding="utf-8"))
            db.executescript((ROOT / "migrations" / "0003_ai_usage_daily.sql").read_text(encoding="utf-8"))
            db.executescript((ROOT / "migrations" / "0004_automation_queue.sql").read_text(encoding="utf-8"))
            db.executescript((ROOT / "migrations" / "0005_owner_daily_digest.sql").read_text(encoding="utf-8"))

            values = (
                "tiktok:7616247520657771796", "tiktok", "7616247520657771796",
                "https://www.tiktok.com/@creator/video/7616247520657771796",
                "@creator", "Poppy edit", "caption", None, "video", 1, "termux_phone", "{}",
            )
            db.execute(
                "INSERT INTO automation_queue (id, platform, external_id, canonical_url, source_key, title, caption, published_at, media_type, freshness_verified, method, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            self.assertEqual(
                db.execute("SELECT status, attempt_count FROM automation_queue WHERE id = ?", (values[0],)).fetchone(),
                ("queued", 0),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO automation_queue (id, platform, external_id, canonical_url, source_key) VALUES (?, ?, ?, ?, ?)",
                    ("tiktok:another-id", "tiktok", "7616247520657771796", values[3], "@creator"),
                )
        finally:
            db.close()

    def test_review_and_media_type_constraints_reject_invalid_values(self):
        db = sqlite3.connect(":memory:")
        try:
            db.executescript((ROOT / "migrations" / "0001_init.sql").read_text(encoding="utf-8"))
            db.executescript((ROOT / "migrations" / "0002_brain_metadata.sql").read_text(encoding="utf-8"))
            insert = (
                "INSERT INTO items (id, platform, source_key, external_id, canonical_url, media_type, review_state) "
                "VALUES ('x:1', 'tiktok', 'creator', '1', 'https://www.tiktok.com/@creator/video/1', ?, ?)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(insert, ("unsupported", "pending"))
            db.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(insert, ("video", "auto_approved"))
        finally:
            db.close()


    def test_owner_daily_digest_migration_persists_window_message_and_events(self):
        db = sqlite3.connect(":memory:")
        try:
            for name in (
                "0001_init.sql",
                "0002_brain_metadata.sql",
                "0003_ai_usage_daily.sql",
                "0004_automation_queue.sql",
                "0005_owner_daily_digest.sql",
            ):
                db.executescript((ROOT / "migrations" / name).read_text(encoding="utf-8"))

            tables = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertTrue({"owner_digest_state", "owner_digest_events"}.issubset(tables))

            db.execute(
                "INSERT INTO owner_digest_state "
                "(id, window_id, window_started_at, window_ends_at, window_started_epoch, window_ends_epoch) "
                "VALUES (1, 'window1234567890abcdef', '2026-10-16T07:00:00Z', "
                "'2026-10-17T07:00:00Z', 1792134000000, 1792220400000)"
            )
            db.execute(
                "INSERT INTO owner_digest_events (window_id, category, source, title, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                ("window1234567890abcdef", "ai_error", "code_ai_github_models",
                 "AI repair call failed", "error_code=provider_unavailable"),
            )
            event = db.execute(
                "SELECT category, source, title FROM owner_digest_events WHERE window_id = ?",
                ("window1234567890abcdef",),
            ).fetchone()
            self.assertEqual(event, ("ai_error", "code_ai_github_models", "AI repair call failed"))

            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO owner_digest_state "
                    "(id, window_id, window_started_at, window_ends_at, window_started_epoch, window_ends_epoch) "
                    "VALUES (1, 'duplicatewindow1234', 'x', 'y', 1, 2)"
                )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
