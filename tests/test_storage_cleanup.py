from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent.brain.storage_cleanup import cleanup_stale_sent_media


NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


class FakeStorage:
    root = "/Rubika-Agent"

    def __init__(self, names=()):
        self.names = set(names)
        self.deleted = []

    def list_directory(self, relative_dir=""):
        if relative_dir != "staging":
            raise AssertionError("cleanup may list only staging")
        return sorted(self.names)

    def delete(self, remote_path):
        if not remote_path.startswith("/Rubika-Agent/staging/"):
            raise AssertionError("unsafe delete path")
        self.deleted.append(remote_path)
        self.names.discard(remote_path.rsplit("/", 1)[-1])


def record(status="sent", hours_old=48, storage_key="/Rubika-Agent/staging/a.mp4"):
    sent_at = (NOW - timedelta(hours=hours_old)).isoformat().replace("+00:00", "Z")
    return {"status": status, "sent_at": sent_at, "storage_key": storage_key}


class StorageCleanupTests(unittest.TestCase):
    def test_dry_run_does_not_delete(self):
        storage = FakeStorage(["a.mp4"])
        report = cleanup_stale_sent_media(
            storage, {"items": {"tiktok:1": record()}}, now=NOW
        )
        self.assertEqual(storage.deleted, [])
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["planned_delete_count"], 1)
        self.assertEqual(report["deleted_count"], 0)

    def test_deletes_only_old_confirmed_sent_media(self):
        storage = FakeStorage(["a.mp4", "b.mp4", "c.mp4", "untracked.mp4"])
        state = {"items": {
            "tiktok:1": record("sent", 48, "/Rubika-Agent/staging/a.mp4"),
            "tiktok:2": record("failed", 72, "/Rubika-Agent/staging/b.mp4"),
            "tiktok:3": record("uncertain", 72, "/Rubika-Agent/staging/c.mp4"),
        }}
        report = cleanup_stale_sent_media(storage, state, apply=True, now=NOW)
        self.assertEqual(storage.deleted, ["/Rubika-Agent/staging/a.mp4"])
        self.assertEqual(report["deleted_count"], 1)
        self.assertIn("referenced_by_non_sent_item", report["retained_counts_by_reason"])
        self.assertEqual(report["untracked_video_files_retained"], 1)

    def test_recent_sent_item_is_protected(self):
        storage = FakeStorage(["a.mp4"])
        state = {"items": {"tiktok:1": record("sent", 2)}}
        report = cleanup_stale_sent_media(storage, state, apply=True, now=NOW)
        self.assertEqual(storage.deleted, [])
        self.assertIn("inside_safety_window", report["retained_counts_by_reason"])

    def test_invalid_timestamp_is_protected(self):
        storage = FakeStorage(["a.mp4"])
        state = {"items": {
            "tiktok:1": {"status": "sent", "sent_at": "not-a-date",
                         "storage_key": "/Rubika-Agent/staging/a.mp4"}
        }}
        report = cleanup_stale_sent_media(storage, state, apply=True, now=NOW)
        self.assertEqual(storage.deleted, [])
        self.assertIn("missing_or_invalid_sent_timestamp", report["retained_counts_by_reason"])

    def test_non_staging_files_and_non_media_are_never_eligible(self):
        storage = FakeStorage(["a.mp4", "agent-ledger-20261014T120000000000Z-1234abcd.json"])
        state = {"items": {
            "tiktok:1": record("sent", 72, "/Rubika-Agent/state/a.mp4"),
            "tiktok:2": record("sent", 72, "/Rubika-Agent/staging/notes.json"),
        }}
        report = cleanup_stale_sent_media(storage, state, apply=True, now=NOW)
        self.assertEqual(storage.deleted, [])
        self.assertEqual(report["deleted_count"], 0)

    def test_shared_path_with_any_non_sent_reference_is_protected(self):
        storage = FakeStorage(["a.mp4"])
        state = {"items": {
            "tiktok:1": record("sent", 72),
            "tiktok:2": record("queued", 72),
        }}
        report = cleanup_stale_sent_media(storage, state, apply=True, now=NOW)
        self.assertEqual(storage.deleted, [])
        self.assertIn("referenced_by_non_sent_item", report["retained_counts_by_reason"])

    def test_max_delete_limit_defers_excess_files(self):
        names = [f"{index}.mp4" for index in range(3)]
        state = {"items": {
            f"tiktok:{index}": record("sent", 72, f"/Rubika-Agent/staging/{index}.mp4")
            for index in range(3)
        }}
        storage = FakeStorage(names)
        report = cleanup_stale_sent_media(
            storage, state, apply=True, max_delete=2, now=NOW
        )
        self.assertEqual(report["deleted_count"], 2)
        self.assertEqual(report["deferred_over_limit_count"], 1)
        self.assertEqual(len(storage.deleted), 2)

    def test_cleanup_cannot_be_configured_to_delete_recent_files(self):
        storage = FakeStorage(["a.mp4"])
        with self.assertRaises(ValueError):
            cleanup_stale_sent_media(
                storage, {"items": {"tiktok:1": record()}},
                apply=True, min_age_hours=1, now=NOW,
            )

    def test_invalid_ledger_fails_closed(self):
        storage = FakeStorage(["a.mp4"])
        with self.assertRaises(ValueError):
            cleanup_stale_sent_media(storage, {}, apply=True, now=NOW)
        self.assertEqual(storage.deleted, [])


if __name__ == "__main__":
    unittest.main()
