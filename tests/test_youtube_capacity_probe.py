from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from agent.brain import test_channel, youtube_capacity_probe as probe


class YouTubeCapacityProbeTests(unittest.TestCase):
    def test_probe_ladder_is_increasing_and_is_only_a_test_plan(self):
        values = probe.probe_size_ladder_mib()
        self.assertEqual(values, tuple(sorted(set(values))))
        self.assertEqual(values[0], 1)
        self.assertGreaterEqual(values[-1], 1024)
        self.assertIn(2048, values)
        self.assertEqual(values[-1], 4096)

    def test_probe_requires_the_registered_test_channel(self):
        self.assertFalse(probe.test_channel_is_authorized(""))
        self.assertFalse(probe.test_channel_is_authorized("not-the-test-channel"))
        self.assertFalse(probe.test_channel_is_authorized("main-channel-guid"))

    def test_probe_guard_delegates_to_canonical_test_channel_guard(self):
        with patch.object(
            test_channel, "is_test_channel_guid",
            side_effect=lambda value: str(value or "").strip() == "authorized-test-only",
        ) as canonical:
            self.assertTrue(probe.test_channel_is_authorized(" authorized-test-only "))
            self.assertFalse(probe.test_channel_is_authorized("main-channel"))
        self.assertEqual(canonical.call_count, 2)

    def test_api_transport_errors_are_reduced_to_safe_labels(self):
        token = "do-not-print-this-token"
        with patch.object(probe.requests, "post", side_effect=requests.Timeout(token)):
            result = probe._api_post(token, "getMe", {})
        serialized = json.dumps(result)
        self.assertFalse(result["transport_ok"])
        self.assertNotIn(token, serialized)
        self.assertIsNone(result["http_status"])


    def test_upload_error_is_reduced_to_a_safe_diagnostic_category(self):
        raw = b'{"status":"ERROR","status_det":"FILE_TOO_LARGE","message":"private-token-value"}'
        label = probe._classify_upload_rejection(400, raw)
        self.assertEqual(label, "rubika_upload_size_rejected")
        self.assertNotIn("private-token-value", label)
        self.assertEqual(
            probe._classify_upload_rejection(400, b"Bad Request"),
            "rubika_upload_http_400_rejected",
        )

    def test_ytdlp_failure_classification_never_echoes_raw_output(self):
        self.assertEqual(
            probe._classify_ytdlp_failure("ERROR: Sign in to confirm you're not a bot"),
            "youtube_bot_check",
        )
        self.assertEqual(
            probe._classify_ytdlp_failure("unexpected output containing private-url"),
            "youtube_unknown_failure",
        )

    def test_ytdlp_smoke_download_selects_a_720_class_profile_for_portrait_video(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            metadata = {
                "width": 720,
                "height": 1280,
                "filesize": 12_345,
                "duration": 10,
            }
            commands = []

            def fake_run(command, **_kwargs):
                commands.append(command)
                if "--dump-single-json" in command:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(metadata),
                        stderr="",
                    )
                output_template = command[command.index("--output") + 1]
                Path(output_template.replace("%(ext)s", "mp4")).write_bytes(b"mock-mp4")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with (
                patch.object(probe.shutil, "which", side_effect=lambda tool: "/usr/bin/" + tool),
                patch.object(probe.subprocess, "run", side_effect=fake_run),
                patch.object(probe, "_probe_mp4", return_value={
                    "width": 720, "height": 1280, "duration_seconds": 10.0,
                }),
            ):
                result = probe._probe_youtube_download(root)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["width"], 720)
            self.assertEqual(result["height"], 1280)
            download_command = commands[1]
            self.assertIn(
                "bestvideo[width<=720][height<=1280]+bestaudio/best[width<=720][height<=1280]",
                download_command,
            )
            self.assertIn("--http-chunk-size", download_command)
            self.assertNotIn("--max-filesize", download_command)
            self.assertTrue(all("--ignore-config" in command for command in commands))
            self.assertFalse(list(root.glob("youtube-source-smoke.*")))

    def test_probe_rejects_a_size_below_the_minimum_before_running_ffmpeg(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "too-small.mp4"
            with self.assertRaisesRegex(probe.ProbeError, "probe_target_size_invalid"):
                probe._create_sized_mp4(target, 512)

    def test_disk_reserve_is_a_working_space_buffer_not_a_file_size_ceiling(self):
        self.assertEqual(probe.MIN_FREE_RESERVE_BYTES, 2 * probe.GIB)
        self.assertGreater(probe.probe_size_ladder_mib()[-1] * probe.MIB, 1 * probe.GIB)


if __name__ == "__main__":
    unittest.main()
