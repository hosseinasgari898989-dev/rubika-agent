from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FailedMediaMigrationTests(unittest.TestCase):
    def _db(self):
        db = sqlite3.connect(":memory:")
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            db.executescript(path.read_text(encoding="utf-8"))
        return db

    def test_all_migrations_apply_in_order_and_create_failed_media(self):
        db = self._db()
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(failed_media)").fetchall()}
            self.assertEqual(columns, {
                "id", "source", "job_id", "item_id", "media_type", "storage_key", "share_url",
                "caption", "reason", "size_bytes", "status", "created_at", "updated_at",
            })
            indexes = {row[1]: row[2] for row in db.execute("PRAGMA index_list(failed_media)").fetchall()}
            self.assertEqual(indexes.get("idx_failed_media_storage_key"), 1)
            self.assertIn("idx_failed_media_status_created", indexes)
        finally:
            db.close()

    def test_defaults_unique_storage_key_and_idempotent_reapply(self):
        db = self._db()
        try:
            db.execute(
                "INSERT INTO failed_media (id, source, storage_key, reason) VALUES (?, ?, ?, ?)",
                ("fm_1", "mob_x", "/Rubika-Agent/failed/a.mp4", "mob_x_preview_upload_failed"),
            )
            row = db.execute(
                "SELECT status, media_type, caption, share_url, created_at, updated_at FROM failed_media WHERE id = 'fm_1'"
            ).fetchone()
            self.assertEqual(row[:4], ("stored", "video", "", None))
            self.assertTrue(row[4] and row[5])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO failed_media (id, source, storage_key, reason) VALUES (?, ?, ?, ?)",
                    ("fm_2", "tiktok", "/Rubika-Agent/failed/a.mp4", "x"),
                )
            db.executescript((ROOT / "migrations" / "0012_failed_media.sql").read_text(encoding="utf-8"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM failed_media").fetchone(), (1,))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
