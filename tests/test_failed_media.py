from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.brain import failed_media
from agent.storage.mega import MegaStorage, MegaStorageError
from agent.storage.mega_export import export_public_link

KEY = "/Rubika-Agent/failed/mob_x_job_abcdef12.mp4"


class FakeStorage:
    root = "/Rubika-Agent"

    def __init__(self, existing=(), fail_upload=False):
        self.existing = list(existing)
        self.fail_upload = fail_upload
        self.uploads = []

    def list_directory(self, rel=""):
        return self.existing

    def upload(self, path, rel="staging"):
        if self.fail_upload:
            raise MegaStorageError("megacmd_command_failed_put")
        self.uploads.append((Path(path).name, rel))
        return f"/Rubika-Agent/{rel}/{Path(path).name}"


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def request(self, method, path, payload=None):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((method, path, payload))
        return {"ok": True}


class PreserveFailedMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = Path(self.tmp.name) / "mob_x_media.mp4"
        self.video.write_bytes(b"x" * 4096)

    def run_preserve(self, storage, client, **kw):
        args = dict(source="mob_x", media_type="video", local_path=self.video, caption="cap",
                    reason="mob_x_preview_upload_failed", job_id="job_abcdef12", item_id="mob_official:1")
        args.update(kw)
        return failed_media.preserve_failed_media(client, storage, **args)

    def test_success_uploads_unique_name_exports_and_records(self):
        storage, client = FakeStorage(), FakeClient()
        with mock.patch.object(failed_media, "export_public_link", return_value="https://mega.nz/file/A#B"):
            res = self.run_preserve(storage, client)
        self.assertTrue(res["ok"])
        self.assertEqual(storage.uploads, [("mob_x_job_abcdef12.mp4", "failed")])
        method, path, payload = client.calls[0]
        self.assertEqual((method, path), ("POST", "/api/console/failed-media/record"))
        self.assertEqual(payload["storage_key"], KEY)
        self.assertEqual(payload["share_url"], "https://mega.nz/file/A#B")
        self.assertEqual(payload["size_bytes"], 4096)

    def test_export_failure_still_records(self):
        client = FakeClient()
        with mock.patch.object(failed_media, "export_public_link", side_effect=MegaStorageError("export_link_missing")):
            res = self.run_preserve(FakeStorage(), client)
        self.assertTrue(res["ok"])
        self.assertIsNone(client.calls[0][2]["share_url"])

    def test_existing_remote_file_is_not_uploaded_again(self):
        storage = FakeStorage(existing=["mob_x_job_abcdef12.mp4"])
        with mock.patch.object(failed_media, "export_public_link", return_value="https://mega.nz/file/A#B"):
            res = self.run_preserve(storage, FakeClient())
        self.assertEqual(storage.uploads, [])
        self.assertEqual(res["storage_key"], KEY)

    def test_never_raises_on_upload_or_record_failure_or_bad_input(self):
        res = self.run_preserve(FakeStorage(fail_upload=True), FakeClient())
        self.assertEqual((res["ok"], res["error"]), (False, "failed_media_upload_failed"))
        with mock.patch.object(failed_media, "export_public_link", return_value=None):
            res = self.run_preserve(FakeStorage(), FakeClient(fail=True))
        self.assertEqual((res["stored"], res["recorded"], res["error"]), (True, False, "failed_media_record_failed"))
        self.assertEqual(self.run_preserve(FakeStorage(), FakeClient(), source="x")["error"], "failed_media_invalid_arguments")
        self.assertEqual(self.run_preserve(FakeStorage(), FakeClient(), local_path="/nope/x.mp4")["error"], "failed_media_local_file_missing")
        self.assertFalse(self.run_preserve(mock.Mock(), FakeClient())["ok"])


class MegaExportTests(unittest.TestCase):
    def storage(self, outputs):
        calls = []

        def runner(args, **kw):
            calls.append(list(args))
            rc, out = outputs[min(len(calls), len(outputs)) - 1]
            return subprocess.CompletedProcess(args, rc, out, "")
        s = MegaStorage(runner=runner)
        s.calls = calls
        return s

    def test_parses_url_from_first_command(self):
        s = self.storage([(0, f"Exported {KEY}: https://mega.nz/file/AAA#BBB\n")])
        self.assertEqual(export_public_link(s, KEY), "https://mega.nz/file/AAA#BBB")
        self.assertEqual(s.calls[0], ["mega-export", "-a", "-f", KEY])

    def test_falls_back_to_plain_export(self):
        s = self.storage([(1, ""), (0, f"{KEY} (file) -> https://mega.io/file/CCC#DDD")])
        self.assertEqual(export_public_link(s, KEY), "https://mega.io/file/CCC#DDD")
        self.assertEqual(s.calls[1], ["mega-export", KEY])

    def test_missing_url_raises(self):
        s = self.storage([(0, "nothing here"), (0, "still nothing")])
        with self.assertRaises(MegaStorageError) as ctx:
            export_public_link(s, KEY)
        self.assertEqual(ctx.exception.code, "export_link_missing")

    def test_path_outside_root_rejected(self):
        with self.assertRaises(ValueError):
            export_public_link(self.storage([(0, "")]), "/other/x.mp4")


if __name__ == "__main__":
    unittest.main()
