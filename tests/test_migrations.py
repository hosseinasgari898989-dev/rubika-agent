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
                "agent_config", "sources", "items", "runs", "delivery_attempts", "brain_events"
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


if __name__ == "__main__":
    unittest.main()
