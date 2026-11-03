from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain import e2e_from_report
from agent.brain.rubika_publisher import RubikaDestinationGuardError


class E2EDestinationGuardTests(unittest.TestCase):
    def test_non_test_destination_is_rejected_before_cloud_or_storage_setup(self):
        with (
            patch.dict(os.environ, {"RUBIKA_CHANNEL_GUID": "different-channel"}),
            patch.object(e2e_from_report, "CloudState") as cloud_state,
            patch.object(e2e_from_report, "MegaStorage") as mega_storage,
        ):
            with self.assertRaisesRegex(
                RubikaDestinationGuardError,
                "rubika_destination_not_test_channel",
            ):
                e2e_from_report.run_one(
                    Path("does-not-exist.json"),
                    "tiktok",
                    Path("unused-report.json"),
                )
        cloud_state.assert_not_called()
        mega_storage.assert_not_called()


class FakeFailedMediaStorage:
    root = "/Rubika-Agent"

    def __init__(self, *, fail_upload=False):
        self.fail_upload = fail_upload
        self.existing = set()
        self.uploads = []
        self.uploaded_sizes = []

    def list_directory(self, directory=""):
        assert directory == "failed"
        return sorted(self.existing)

    def upload(self, path, directory="staging"):
        if self.fail_upload:
            raise RuntimeError("mega_upload_failed")
        source = Path(path)
        with source.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        self.uploads.append((source.name, directory, digest))
        self.uploaded_sizes.append(source.stat().st_size)
        self.existing.add(source.name)
        return f"{self.root}/{directory}/{source.name}"


class FakeFailedMediaClient:
    def __init__(self):
        self.rows = {}
        self.calls = []

    def request(self, method, path, payload):
        self.calls.append((method, path, dict(payload)))
        # Mirrors the existing Worker record endpoint's ON CONFLICT(storage_key) upsert.
        self.rows[payload["storage_key"]] = dict(payload)
        return {"ok": True}


class TikTokFailedMediaPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.original = Path(self.temp.name) / "raw" / "tiktok_7616247520657771796.mp4"
        self.original.parent.mkdir()
        self.original.write_bytes(b"untouched-source-video" * 256)
        self.item_id = "tiktok:7616247520657771796"
        self.delivery = {"status": "uncertain", "error": "transport_RuntimeError"}

    def digest(self):
        with self.original.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()

    def preserve(self, storage, client, delivery=None):
        return e2e_from_report._preserve_failed_tiktok_delivery(
            platform="tiktok",
            client=client,
            storage=storage,
            original_path=self.original,
            caption="Poppy Playtime edit",
            item_id=self.item_id,
            delivery=delivery or self.delivery,
        )

    def test_rubika_upload_failure_preserves_untouched_original(self):
        before = self.digest()
        storage, client = FakeFailedMediaStorage(), FakeFailedMediaClient()
        with patch("agent.brain.failed_media.export_public_link",
                   return_value="https://mega.nz/file/failed#key"):
            result = self.preserve(storage, client)

        self.assertTrue(result["ok"])
        self.assertTrue(result["stored"])
        self.assertTrue(result["recorded"])
        self.assertEqual(storage.uploads[0][1], "failed")
        self.assertEqual(storage.uploads[0][2], before)
        self.assertEqual(client.rows[next(iter(client.rows))]["source"], "tiktok")
        self.assertEqual(client.rows[next(iter(client.rows))]["reason"], "transport_runtimeerror")
        self.assertEqual(self.digest(), before)

    def test_video_over_50_mib_is_preserved_without_reencoding_or_truncation(self):
        self.original.write_bytes(b"")
        size = 50 * 1024 * 1024 + 1
        with self.original.open("wb") as handle:
            handle.truncate(size)
        before = self.digest()
        storage, client = FakeFailedMediaStorage(), FakeFailedMediaClient()
        delivery = {"status": "uncertain", "error": "rubika_upload_server_error"}

        with patch("agent.brain.failed_media.export_public_link", return_value=None):
            result = self.preserve(storage, client, delivery)

        self.assertTrue(result["ok"])
        self.assertEqual(storage.uploaded_sizes, [size])
        self.assertEqual(next(iter(client.rows.values()))["size_bytes"], size)
        self.assertEqual(storage.uploads[0][2], before)
        self.assertEqual(self.digest(), before)

    def test_explicit_rubika_send_rejection_is_preserved_after_delivery_retries(self):
        storage, client = FakeFailedMediaStorage(), FakeFailedMediaClient()
        delivery = {"status": "failed", "attempts": 3, "error": "rubika_rejected_error"}
        with patch("agent.brain.failed_media.export_public_link", return_value=None):
            result = self.preserve(storage, client, delivery)
        self.assertTrue(result["ok"])
        self.assertEqual(next(iter(client.rows.values()))["reason"], "rubika_rejected_error")

    def test_preservation_failure_does_not_replace_original_delivery_error(self):
        storage, client = FakeFailedMediaStorage(fail_upload=True), FakeFailedMediaClient()
        delivery = {"status": "failed", "attempts": 3, "error": "rubika_rejected_error"}
        before = self.digest()
        with patch("agent.brain.failed_media.export_public_link", return_value=None):
            result = self.preserve(storage, client, delivery)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "failed_media_upload_failed")
        self.assertEqual(delivery, {
            "status": "failed", "attempts": 3, "error": "rubika_rejected_error",
        })
        self.assertEqual(client.rows, {})
        self.assertEqual(self.digest(), before)

    def test_unexpected_preserver_exception_is_non_fatal_and_delivery_error_survives(self):
        delivery = {"status": "uncertain", "error": "rubika_upload_timeout"}
        with patch.object(e2e_from_report, "preserve_failed_media",
                          side_effect=RuntimeError("preserve exploded")):
            result = self.preserve(FakeFailedMediaStorage(), FakeFailedMediaClient(), delivery)
        self.assertEqual(result["error"], "failed_media_preserve_unexpected_error")
        self.assertEqual(delivery["error"], "rubika_upload_timeout")
        self.assertEqual(delivery["status"], "uncertain")

    def test_duplicate_retry_reuses_failed_file_and_worker_upsert_keeps_one_row(self):
        storage, client = FakeFailedMediaStorage(), FakeFailedMediaClient()
        with patch("agent.brain.failed_media.export_public_link", return_value=None):
            first = self.preserve(storage, client)
            second = self.preserve(storage, client)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(len(storage.uploads), 1)
        self.assertEqual(len(client.rows), 1)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(self.digest(), storage.uploads[0][2])

    def test_already_sent_item_is_never_preserved(self):
        storage, client = FakeFailedMediaStorage(), FakeFailedMediaClient()
        with patch.object(e2e_from_report, "preserve_failed_media") as mocked:
            result = self.preserve(storage, client, {"status": "sent", "message_id": "msg-1"})
        self.assertIsNone(result)
        mocked.assert_not_called()
        self.assertEqual(storage.uploads, [])
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
