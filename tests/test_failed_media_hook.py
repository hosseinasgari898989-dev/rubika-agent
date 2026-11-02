from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from agent.brain import mob_x_link_processor as proc

JOB = {
    "id": "job_abcdef1234", "external_id": "2107878755079213476",
    "params": {"url": "https://x.com/mobgamesstudios/status/2107878755079213476"},
}


class FailedMediaHookTests(unittest.TestCase):
    def run_job(self, preview_side_effect, *, force_fail=None):
        cloud = mock.Mock()
        cloud.get_seen_index.return_value = {"ids": set()}
        client = mock.Mock()
        client.rules.return_value = {"translate_enabled": False}
        storage = mock.Mock()
        storage.upload.return_value = "/Rubika-Agent/official/x.jpg"
        # patch.multiple() with explicit values does not return the mocks, so keep them here.
        mocks = dict(
            CloudState=mock.Mock(return_value=cloud),
            fetch_public_post=mock.Mock(return_value={"text": "", "title": "", "published_at": None}),
            select_media=mock.Mock(return_value=("image", "https://pbs.twimg.com/x.jpg")),
            download_public_media=mock.Mock(return_value=Path("/tmp/mob_x_media.jpg")),
            inspect_media=mock.Mock(return_value=mock.Mock(ok=True, errors=[], duration_seconds=0, has_audio=False)),
            evaluate_official_media=mock.Mock(return_value=mock.Mock(decision="eligible")),
            upload_preview_media=mock.Mock(side_effect=preview_side_effect),
            send_owner_preview=mock.Mock(return_value="msg_1"),
            preserve_failed_media=mock.Mock(return_value={"ok": True}),
        )
        env_values = {"RUBIKA_TOKEN": "t" * 40, "RUBIKA_OWNER_CHAT_ID": "c1"}
        if force_fail is not None:
            env_values["MOB_X_TEST_FORCE_PREVIEW_FAIL"] = force_fail
        env = mock.patch.dict("os.environ", env_values, clear=True)
        err = None
        with mock.patch.multiple(proc, **mocks), env:
            try:
                proc.process_job(JOB, client, storage=storage)
            except proc.MobXLinkError as exc:
                err = exc
        return err, mocks, client

    def test_preview_upload_failure_preserves_original_then_raises_same_error(self):
        err, p, client = self.run_job(RuntimeError("rubika 5xx"))
        self.assertIsNotNone(err)
        self.assertEqual(err.code, "mob_x_preview_upload_failed")
        p["preserve_failed_media"].assert_called_once()
        args, kwargs = p["preserve_failed_media"].call_args
        self.assertIs(args[0], client)
        self.assertIn("mob_x", args)
        self.assertIn("mob_x_preview_upload_failed", args)
        self.assertEqual(kwargs.get("job_id"), "job_abcdef1234")

    def test_preserve_not_called_when_upload_succeeds(self):
        err, p, _ = self.run_job(lambda *a, **k: "file_id_1")
        self.assertIsNone(err)
        p["upload_preview_media"].assert_called_once()
        p["preserve_failed_media"].assert_not_called()

    def test_exact_test_flag_forces_preview_failure_and_preserves_media(self):
        with mock.patch("builtins.print") as log:
            err, p, client = self.run_job(
                lambda *a, **k: self.fail("Rubika upload must be skipped when the test flag is enabled"),
                force_fail="1",
            )

        self.assertIsNotNone(err)
        self.assertEqual(err.code, "mob_x_preview_upload_failed")
        p["upload_preview_media"].assert_not_called()
        p["preserve_failed_media"].assert_called_once()
        args, kwargs = p["preserve_failed_media"].call_args
        self.assertIs(args[0], client)
        self.assertIn("mob_x_preview_upload_failed", args)
        self.assertEqual(kwargs.get("job_id"), "job_abcdef1234")
        self.assertTrue(any(
            "mob_x_test_force_preview_fail" in call.args[0]
            and "MOB_X_TEST_FORCE_PREVIEW_FAIL" in call.args[0]
            and '"enabled": true' in call.args[0]
            for call in log.call_args_list
        ))

    def test_zero_test_flag_keeps_existing_upload_path(self):
        err, p, _ = self.run_job(lambda *a, **k: "file_id_1", force_fail="0")
        self.assertIsNone(err)
        p["upload_preview_media"].assert_called_once()
        p["preserve_failed_media"].assert_not_called()

    def test_missing_test_flag_keeps_existing_upload_path(self):
        err, p, _ = self.run_job(lambda *a, **k: "file_id_1", force_fail=None)
        self.assertIsNone(err)
        p["upload_preview_media"].assert_called_once()
        p["preserve_failed_media"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
