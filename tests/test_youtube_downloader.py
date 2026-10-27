from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.brain import youtube_downloader as downloader


def _metadata() -> dict:
    return {
        "id": "abcdefghijk",
        "width": 1920,
        "height": 1080,
        "duration": 100,
        "formats": [
            {
                "format_id": "video720",
                "width": 1280,
                "height": 720,
                "vcodec": "avc1",
                "acodec": "none",
                "tbr": 1500,
                "filesize_approx": 15_000_000,
            },
            {
                "format_id": "audio",
                "width": None,
                "height": None,
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "abr": 128,
                "filesize_approx": 2_000_000,
            },
        ],
    }


class YouTubeDownloaderTests(unittest.TestCase):
    def test_estimates_size_from_the_720p_video_and_audio_formats(self):
        size, basis = downloader.estimate_download_size(
            _metadata(),
            max_width=1280,
            max_height=720,
        )
        self.assertEqual(size, 17_000_000)
        self.assertEqual(basis, "selected_video_audio_formats")

    def test_estimate_does_not_reuse_size_of_a_higher_resolution_top_level_format(self):
        metadata = {
            **_metadata(),
            "filesize_approx": 2_000_000_000,
        }
        size, basis = downloader.estimate_download_size(
            metadata,
            max_width=1280,
            max_height=720,
        )
        self.assertEqual(size, 17_000_000)
        self.assertEqual(basis, "selected_video_audio_formats")

    def test_download_normalizes_output_and_does_not_attempt_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            calls = []

            def fake_run(command, **_kwargs):
                calls.append(command)
                base = Path(command[0]).name
                if "--dump-single-json" in command:
                    return SimpleNamespace(returncode=0, stdout=json.dumps(_metadata()), stderr="")
                if base == "yt-dlp":
                    template = command[command.index("--output") + 1]
                    Path(template.replace("%(ext)s", "mp4")).write_bytes(b"mock-source-media")
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if base == "ffprobe":
                    target = Path(command[-1])
                    portrait = target.name.endswith(".720p.mp4")
                    dimensions = (
                        {"width": 1280, "height": 720}
                        if portrait
                        else {"width": 1280, "height": 720}
                    )
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps({
                            "streams": [
                                {"codec_type": "video", "codec_name": "h264", **dimensions},
                                {"codec_type": "audio", "codec_name": "aac"},
                            ],
                            "format": {"duration": "100.0"},
                        }),
                        stderr="",
                    )
                if base == "ffmpeg":
                    Path(command[-1]).write_bytes(b"mock-final-media")
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                raise AssertionError("Unexpected subprocess: " + base)

            with (
                patch.object(
                    downloader.shutil, "which",
                    side_effect=lambda tool: "/usr/bin/" + tool if tool in {"yt-dlp", "ffmpeg", "ffprobe"} else None,
                ),
                patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=12 * 1024**3)),
                patch.object(downloader.subprocess, "run", side_effect=fake_run),
            ):
                result = downloader.download_youtube_video(
                    "https://www.youtube.com/watch?v=abcdefghijk",
                    root,
                    max_runtime_seconds=600,
                )

            output = Path(result["output_path"])
            self.assertTrue(output.is_file())
            self.assertEqual(result["status"], "downloaded_and_normalized")
            self.assertFalse(result["uploaded"])
            self.assertFalse(result["published"])
            self.assertEqual(result["source"]["width"], 1280)
            self.assertEqual(result["output"]["width"], 1280)
            self.assertEqual(result["output"]["height"], 720)
            self.assertEqual(result["rubika_upload_guard"]["status"], "rubika_upload_limit_unverified")
            self.assertEqual(result["rubika_upload_guard"]["allowed"], False)
            self.assertTrue(any("--http-chunk-size" in command for command in calls))
            self.assertFalse(any("sendFile" in command for command in calls))
            self.assertFalse(list(root.glob(".abcdefghijk-source-*")))

    def test_insufficient_disk_stops_before_ytdlp_download(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []

            def fake_run(command, **_kwargs):
                calls.append(command)
                return SimpleNamespace(returncode=0, stdout=json.dumps(_metadata()), stderr="")

            with (
                patch.object(
                    downloader.shutil, "which",
                    side_effect=lambda tool: "/usr/bin/" + tool if tool in {"yt-dlp", "ffmpeg"} else None,
                ),
                patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=1_000_000_000)),
                patch.object(downloader.subprocess, "run", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(
                    downloader.YouTubeDownloadError,
                    "youtube_insufficient_disk_space",
                ):
                    downloader.download_youtube_video(
                        "https://www.youtube.com/watch?v=abcdefghijk",
                        folder,
                        max_runtime_seconds=600,
                    )

            self.assertEqual(len(calls), 1)
            self.assertIn("--dump-single-json", calls[0])

    def test_untrusted_url_is_rejected_before_external_process(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(downloader.subprocess, "run") as run:
                with self.assertRaisesRegex(
                    downloader.YouTubeDownloadError,
                    "youtube_video_url_invalid",
                ):
                    downloader.download_youtube_video(
                        "https://youtube.com.evil.example/watch?v=abcdefghijk",
                        folder,
                    )
            run.assert_not_called()

    def test_runtime_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(downloader.subprocess, "run") as run:
                with self.assertRaisesRegex(
                    downloader.YouTubeDownloadError,
                    "youtube_runtime_limit_invalid",
                ):
                    downloader.download_youtube_video(
                        "https://youtu.be/abcdefghijk",
                        folder,
                        max_runtime_seconds=999_999,
                    )
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
