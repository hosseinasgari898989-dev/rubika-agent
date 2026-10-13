from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.brain.ledger import MegaLedger, new_state


class FakeVersionedStorage:
    def __init__(self):
        self.files = {}
        self.root = "/Rubika-Agent"

    def list_directory(self, relative_dir=""):
        prefix = self.root + ("/" + relative_dir.strip("/") if relative_dir else "")
        prefix = prefix.rstrip("/") + "/"
        return sorted(path[len(prefix):] for path in self.files if path.startswith(prefix) and "/" not in path[len(prefix):])

    def upload(self, local_path, relative_dir="staging"):
        source = Path(local_path)
        remote_dir = self.root + "/" + relative_dir.strip("/")
        remote = remote_dir + "/" + source.name
        self.files[remote] = source.read_bytes()
        return remote

    def download(self, remote_path, destination_dir):
        if remote_path not in self.files:
            raise FileNotFoundError("remote_not_found")
        folder = Path(destination_dir)
        folder.mkdir(parents=True, exist_ok=True)
        result = folder / remote_path.rsplit("/", 1)[-1]
        result.write_bytes(self.files[remote_path])
        return result

    def delete(self, remote_path):
        self.files.pop(remote_path, None)


class MegaLedgerTests(unittest.TestCase):
    def test_empty_ledger_has_stable_schema(self):
        ledger = MegaLedger(FakeVersionedStorage())
        state = ledger.load()
        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["items"], {})
        self.assertEqual(state["runs"], [])
        self.assertEqual(state["events"], [])

    def test_snapshot_is_uploaded_verified_and_read_back(self):
        storage = FakeVersionedStorage()
        ledger = MegaLedger(storage)
        state = new_state()
        MegaLedger.upsert_item(state, "tiktok:123", {
            "platform": "tiktok", "external_id": "123", "sha256": "abc",
            "status": "queued",
        })
        result = ledger.save(state)
        self.assertTrue(result["ok"])
        loaded = ledger.load()
        self.assertEqual(loaded["items"]["tiktok:123"]["sha256"], "abc")
        self.assertTrue(result["remote_path"].startswith("/Rubika-Agent/state/"))

    def test_duplicate_source_id_and_hash_are_detected(self):
        state = new_state()
        MegaLedger.upsert_item(state, "tiktok:123", {
            "platform": "tiktok", "external_id": "123", "sha256": "abc",
        })
        self.assertTrue(MegaLedger.is_duplicate(
            state, platform="tiktok", external_id="123"
        ))
        self.assertTrue(MegaLedger.is_duplicate(
            state, platform="instagram", external_id="other", sha256="abc"
        ))
        self.assertFalse(MegaLedger.is_duplicate(
            state, platform="youtube", external_id="different", sha256="different"
        ))

    def test_delivery_attempt_history_and_three_attempt_stop(self):
        state = new_state()
        MegaLedger.upsert_item(state, "instagram:abc", {
            "platform": "instagram", "external_id": "abc", "status": "queued",
        })
        for number in range(1, 4):
            MegaLedger.record_attempt(
                state, "instagram:abc", attempt_number=number, status="failed",
                error_code="rejected",
            )
        item = state["items"]["instagram:abc"]
        self.assertEqual(item["attempt_count"], 3)
        self.assertEqual(len(item["attempts"]), 3)
        self.assertEqual(item["status"], "failed")

    def test_uncertain_or_sent_state_is_not_reset_to_queued_on_upsert(self):
        state = new_state()
        MegaLedger.upsert_item(state, "youtube:xyz", {
            "platform": "youtube", "external_id": "xyz", "status": "sent",
        })
        MegaLedger.upsert_item(state, "youtube:xyz", {"status": "queued"})
        self.assertEqual(state["items"]["youtube:xyz"]["status"], "sent")
        state["items"]["youtube:xyz"]["status"] = "uncertain"
        MegaLedger.upsert_item(state, "youtube:xyz", {"status": "queued"})
        self.assertEqual(state["items"]["youtube:xyz"]["status"], "uncertain")

    def test_save_keeps_only_the_newest_configured_versions(self):
        storage = FakeVersionedStorage()
        ledger = MegaLedger(storage, keep_versions=3)
        for number in range(5):
            state = new_state()
            state["events"].append({"code": "run-" + str(number)})
            ledger.save(state)
        names = storage.list_directory("state")
        self.assertEqual(len(names), 3)
        self.assertEqual(ledger.load()["events"][-1]["code"], "run-4")


if __name__ == "__main__":
    unittest.main()
