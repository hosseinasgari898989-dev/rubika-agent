"""Exercise Mob X duplicate SQL and owner-link confirmation against real SQLite schema."""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
POST_ID = "2111111111111111111"
CANONICAL_URL = f"https://x.com/mobgamesstudios/status/{POST_ID}"
TWITTER_URL = f"https://twitter.com/mobgamesstudios/status/{POST_ID}"


def _normalize_external_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.startswith("mob_official:"):
        normalized = normalized[len("mob_official:"):]
    if normalized.startswith("x-"):
        normalized = normalized[2:]
    return normalized if normalized.isdigit() and 15 <= len(normalized) <= 22 else None


def _find_duplicate(
    db: sqlite3.Connection,
    value: str,
    *,
    exclude_item_id: str | None = None,
    exclude_job_id: str | None = None,
    exclude_panel_link_id: int | None = None,
) -> dict[str, Any]:
    """Use the same SQL predicates, bind order, and optional exclusions as src/mob_x_identity.js."""
    post_id = _normalize_external_id(value)
    if post_id is None:
        return {"duplicate": False, "invalid": True}

    prefixed = "x-" + post_id
    item_ids = ["mob_official:" + post_id, "mob_official:" + prefixed]

    item_sql = (
        "SELECT id, status, external_id FROM items WHERE platform = 'mob_official' "
        "AND (external_id IN (?, ?) OR id IN (?, ?))"
    )
    item_args: list[Any] = [post_id, prefixed, *item_ids]
    if exclude_item_id:
        item_sql += " AND id <> ?"
        item_args.append(exclude_item_id)
    item_sql += " LIMIT 1"
    item = db.execute(item_sql, item_args).fetchone()

    job_sql = (
        "SELECT id, status FROM console_jobs WHERE type = 'mob_x_link' AND ("
        "(CASE WHEN json_valid(result_json) THEN json_extract(result_json, '$.external_id') END IN (?, ?)) OR "
        "(CASE WHEN json_valid(result_json) THEN json_extract(result_json, '$.item_id') END IN (?, ?)) OR "
        "(CASE WHEN json_valid(params_json) THEN json_extract(params_json, '$.url') END IN (?, ?)))"
    )
    canonical_url = "https://x.com/mobgamesstudios/status/" + post_id
    twitter_url = "https://twitter.com/mobgamesstudios/status/" + post_id
    job_args: list[Any] = [post_id, prefixed, *item_ids, canonical_url, twitter_url]
    if exclude_job_id:
        job_sql += " AND id <> ?"
        job_args.append(exclude_job_id)
    job_sql += " LIMIT 1"
    job = db.execute(job_sql, job_args).fetchone()

    link_sql = "SELECT id, status FROM panel_links WHERE external_id IN (?, ?)"
    link_args: list[Any] = [post_id, prefixed]
    if exclude_panel_link_id is not None:
        link_sql += " AND id <> ?"
        link_args.append(exclude_panel_link_id)
    link_sql += " LIMIT 1"
    link = db.execute(link_sql, link_args).fetchone()

    return {
        "duplicate": bool(item or job or link),
        "item": item,
        "job": job,
        "link": link,
    }


def _database() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    migration_files = sorted(MIGRATIONS.glob("*.sql"))
    if not migration_files:
        raise AssertionError("No SQL migrations were found")
    for migration in migration_files:
        db.executescript(migration.read_text(encoding="utf-8"))
    return db


class MobXDuplicateSqliteTests(unittest.TestCase):
    def test_fresh_manual_link_confirms_and_its_own_link_is_not_a_duplicate(self) -> None:
        db = _database()
        try:
            db.execute(
                "INSERT INTO panel_links (url, external_id, status) VALUES (?, ?, 'pending')",
                (CANONICAL_URL, POST_ID),
            )
            link_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])

            duplicate = _find_duplicate(db, POST_ID, exclude_panel_link_id=link_id)
            self.assertFalse(duplicate["duplicate"], "the pending link must exclude itself")

            # These are the same writes used by the p_mob_confirm path in src/owner_panel.js.
            changed = db.execute(
                "UPDATE panel_links SET status = 'confirmed', "
                "confirmed_at = COALESCE(confirmed_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ? AND status IN ('pending', 'confirmed')",
                (link_id,),
            )
            self.assertEqual(changed.rowcount, 1)

            db.execute(
                "INSERT INTO console_jobs "
                "(id, type, status, progress, params_json, result_json, error, panel_link_id) "
                "SELECT ?, 'mob_x_link', 'confirmed', 0, ?, NULL, NULL, ? "
                "WHERE EXISTS (SELECT 1 FROM panel_links WHERE id = ? AND status = 'confirmed') "
                "ON CONFLICT(panel_link_id) WHERE type = 'mob_x_link' AND panel_link_id IS NOT NULL DO NOTHING",
                (
                    "job_sqlite_fresh_001",
                    json.dumps({"url": CANONICAL_URL, "panel_link_id": link_id}),
                    link_id,
                    link_id,
                ),
            )
            row = db.execute(
                "SELECT status FROM panel_links WHERE id = ?", (link_id,)
            ).fetchone()
            jobs = db.execute(
                "SELECT id, status FROM console_jobs WHERE panel_link_id = ? AND type = 'mob_x_link'",
                (link_id,),
            ).fetchall()
            self.assertEqual(row["status"], "confirmed")
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["status"], "confirmed")

            # Once the job exists, excluding its own row and its link still permits
            # the idempotent confirmation path to recognize that same job, not a duplicate.
            own_job = _find_duplicate(
                db, POST_ID, exclude_job_id="job_sqlite_fresh_001",
                exclude_panel_link_id=link_id,
            )
            self.assertFalse(own_job["duplicate"])
        finally:
            db.close()

    def test_plain_and_x_prefixed_sent_item_and_job_ids_are_duplicates(self) -> None:
        for stored_id in (POST_ID, "x-" + POST_ID):
            with self.subTest(stored_id=stored_id):
                db = _database()
                try:
                    item_id = "mob_official:" + stored_id
                    db.execute(
                        "INSERT INTO items (id, platform, source_key, external_id, canonical_url, status) "
                        "VALUES (?, 'mob_official', 'x.com/mobgamesstudios', ?, ?, 'sent')",
                        (item_id, stored_id, CANONICAL_URL),
                    )
                    duplicate_item = _find_duplicate(db, POST_ID)
                    self.assertTrue(duplicate_item["duplicate"])
                    self.assertEqual(duplicate_item["item"]["id"], item_id)

                    job_id = "job_sqlite_" + ("prefixed" if stored_id.startswith("x-") else "plain") + "_001"
                    db.execute(
                        "INSERT INTO console_jobs (id, type, status, params_json, result_json) "
                        "VALUES (?, 'mob_x_link', 'sent', ?, ?)",
                        (
                            job_id,
                            json.dumps({"url": TWITTER_URL}),
                            json.dumps({"external_id": stored_id, "item_id": item_id}),
                        ),
                    )
                    duplicate_job = _find_duplicate(db, POST_ID)
                    self.assertTrue(duplicate_job["duplicate"])
                    self.assertEqual(duplicate_job["job"]["id"], job_id)
                finally:
                    db.close()

    def test_each_confirmation_row_exclusion_uses_its_own_primary_key(self) -> None:
        # The same post is present in all three tables; each matching row is excluded
        # only by the corresponding ID form, just as the Worker does for an owner action.
        db = _database()
        try:
            db.execute(
                "INSERT INTO items (id, platform, source_key, external_id, canonical_url, status) "
                "VALUES (?, 'mob_official', 'x.com/mobgamesstudios', ?, ?, 'ready')",
                ("mob_official:x-" + POST_ID, "x-" + POST_ID, CANONICAL_URL),
            )
            db.execute(
                "INSERT INTO panel_links (url, external_id, status) VALUES (?, ?, 'confirmed')",
                (CANONICAL_URL, POST_ID),
            )
            link_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
            db.execute(
                "INSERT INTO console_jobs (id, type, status, params_json, result_json, panel_link_id) "
                "VALUES (?, 'mob_x_link', 'ready_for_review', ?, ?, ?)",
                (
                    "job_sqlite_own_001",
                    json.dumps({"url": CANONICAL_URL}),
                    json.dumps({
                        "external_id": POST_ID,
                        "item_id": "mob_official:x-" + POST_ID,
                    }),
                    link_id,
                ),
            )

            item_only = _find_duplicate(db, POST_ID, exclude_item_id="mob_official:x-" + POST_ID)
            self.assertTrue(item_only["duplicate"], "the job and link remain independent blockers")
            job_only = _find_duplicate(db, POST_ID, exclude_job_id="job_sqlite_own_001")
            self.assertTrue(job_only["duplicate"], "the item and link remain independent blockers")
            link_only = _find_duplicate(db, POST_ID, exclude_panel_link_id=link_id)
            self.assertTrue(link_only["duplicate"], "the item and job remain independent blockers")

            all_own_rows = _find_duplicate(
                db,
                POST_ID,
                exclude_item_id="mob_official:x-" + POST_ID,
                exclude_job_id="job_sqlite_own_001",
                exclude_panel_link_id=link_id,
            )
            self.assertFalse(all_own_rows["duplicate"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
