from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.brain import e2e_from_report, failed_media


class PreservationSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.original = self.raw / "tiktok_7616247520657771796.mp4"
        self.original_bytes = b"untouched-source-video\x00\x01\x02"
        self.original.write_bytes(self.original_bytes)

    def _call(self, delivery):
        return e2e_from_report._preserve_failed_tiktok_original(
            client=object(), storage=object(), original_path=self.original,
            item_id="tiktok:7616247520657771796", caption="edit caption",
            delivery=delivery,
        )

    def test_rubika_upload_failure_preserves_untouched_original(self):
        delivery = {
            "status": "uncertain",
            "error": "transport_RubikaUploadError",
            "attempts": 1,
            "cleanup": "retained",
        }
        with mock.patch.object(
            e2e_from_report, "preserve_failed_media",
            return_value={"ok": True, "stored": True, "recorded": True, "error": None},
        ) as preserve:
            result = self._call(delivery)

        self.assertEqual(result, {
            "attempted": True, "ok": True, "stored": True, "recorded": True, "error": None,
        })
        args, kwargs = preserve.call_args
        self.assertEqual(args[2:4], ("tiktok", "video"))
        self.assertEqual(Path(args[4]), self.original)
        self.assertEqual(kwargs, {"job_id": None, "item_id": "tiktok:7616247520657771796"})
        self.assertEqual(self.original.read_bytes(), self.original_bytes)
        self.assertEqual(delivery["error"], "transport_RubikaUploadError")

    def test_over_50_mib_rubika_rejection_preserves_original(self):
        oversized = self.raw / "tiktok_7616247520657771796.mp4"
        with oversized.open("wb") as stream:
            stream.truncate(50 * 1024 * 1024 + 1)
        self.assertGreater(oversized.stat().st_size, 50 * 1024 * 1024)
        with mock.patch.object(
            e2e_from_report, "preserve_failed_media",
            return_value={"ok": True, "stored": True, "recorded": True, "error": None},
        ) as preserve:
            result = self._call({
                "status": "failed", "error": "rubika_rejected_file_too_large",
                "attempts": 3, "cleanup": "retained",
            })
        self.assertTrue(result["ok"])
        self.assertEqual(Path(preserve.call_args.args[4]), oversized)
        self.assertEqual(oversized.stat().st_size, 50 * 1024 * 1024 + 1)

    def test_preservation_failure_does_not_replace_original_delivery_error(self):
        delivery = {
            "status": "failed", "error": "rubika_rejected_upload_failed",
            "attempts": 3, "cleanup": "retained",
        }
        with mock.patch.object(
            e2e_from_report, "preserve_failed_media",
            side_effect=RuntimeError("fake Mega failure"),
        ):
            result = self._call(delivery)
        self.assertEqual(delivery["status"], "failed")
        self.assertEqual(delivery["error"], "rubika_rejected_upload_failed")
        self.assertEqual(result["error"], "failed_media_internal_error")
        self.assertFalse(result["ok"])
        self.assertEqual(self.original.read_bytes(), self.original_bytes)

    def test_original_file_check_error_does_not_hide_delivery_error(self):
        delivery = {
            "status": "failed", "error": "rubika_rejected_upload_failed",
            "attempts": 3, "cleanup": "retained",
        }
        with mock.patch.object(Path, "is_file", side_effect=OSError("local storage error")):
            result = self._call(delivery)
        self.assertEqual(delivery["status"], "failed")
        self.assertEqual(delivery["error"], "rubika_rejected_upload_failed")
        self.assertEqual(result["error"], "failed_media_internal_error")
        self.assertFalse(result["ok"])

    def test_confirmed_sent_item_is_never_preserved(self):
        with mock.patch.object(e2e_from_report, "preserve_failed_media") as preserve:
            result = self._call({
                "status": "sent", "message_id": "confirmed-message", "attempts": 1,
            })
        preserve.assert_not_called()
        self.assertEqual(result, {"attempted": False, "reason": "delivery_confirmed"})
        self.assertEqual(self.original.read_bytes(), self.original_bytes)


class IdempotentTikTokPreservationTests(unittest.TestCase):
    class Storage:
        root = "/Rubika-Agent"

        def __init__(self):
            self.files = {}
            self.uploads = []

        def list_directory(self, rel=""):
            return sorted(self.files)

        def upload(self, path, rel="staging"):
            name = Path(path).name
            self.uploads.append((name, rel))
            self.files[name] = Path(path).read_bytes()
            return f"{self.root}/{rel}/{name}"

    class Client:
        def __init__(self):
            self.rows = {}
            self.requests = []

        def request(self, method, path, payload=None):
            self.requests.append((method, path, payload))
            if method == "POST" and path == "/api/console/failed-media/record":
                # Mirrors the Worker endpoint's ON CONFLICT(storage_key) upsert.
                self.rows[payload["storage_key"]] = dict(payload)
            return {"ok": True}

    def test_duplicate_retry_uploads_one_original_and_keeps_one_record(self):
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "tiktok-original.mp4"
            original_bytes = b"the same untouched original"
            original.write_bytes(original_bytes)
            storage, client = self.Storage(), self.Client()
            results = []
            with mock.patch.object(failed_media, "export_public_link", return_value=None):
                for _ in range(2):
                    results.append(failed_media.preserve_failed_media(
                        client, storage, source="tiktok", media_type="video",
                        local_path=original, caption="edit caption",
                        reason="rubika_upload_failed", job_id=None,
                        item_id="tiktok:7616247520657771796",
                    ))
            self.assertTrue(all(row["ok"] for row in results))
            self.assertEqual(len(storage.uploads), 1)
            self.assertEqual(len(client.rows), 1)
            self.assertEqual(len(client.requests), 2)
            self.assertEqual(next(iter(storage.files.values())), original_bytes)
            self.assertEqual(original.read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()
