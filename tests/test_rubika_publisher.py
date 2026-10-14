from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.brain.rubika_publisher import RubikaPublisher


class FakeBot:
    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.calls = []

    def send_image(self, **kwargs):
        self.calls.append(("send_image", kwargs))
        return {"status": "OK"}

    def send_video(self, **kwargs):
        self.calls.append(("send_video", kwargs))
        return {"status": "OK"}

    def send_file(self, **kwargs):
        self.calls.append(("send_file", kwargs))
        return {"status": "OK"}


class RubikaPublisherMediaTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.media_path = Path(self.tempdir.name) / "official_media.webp"
        self.media_path.write_bytes(b"fake-media-for-adapter-test")
        self.fake_bot = FakeBot()
        self.publisher = RubikaPublisher(
            "test-token",
            "test-channel",
            bot_factory=lambda **kwargs: self.fake_bot,
        )

    def test_image_uses_native_inline_image_method_with_caption(self):
        self.publisher.send_media(self.media_path, "image", "A test caption")

        self.assertEqual(len(self.fake_bot.calls), 1)
        method, payload = self.fake_bot.calls[0]
        self.assertEqual(method, "send_image")
        self.assertEqual(payload["chat_id"], "test-channel")
        self.assertEqual(payload["path"], str(self.media_path))
        self.assertEqual(payload["text"], "A test caption")
        self.assertEqual(payload["file_name"], "official_media.webp")

    def test_image_never_falls_back_to_generic_file_attachment(self):
        self.publisher.send_media(self.media_path, "image", "A test caption")
        self.assertNotIn("send_file", [method for method, _ in self.fake_bot.calls])

    def test_video_uses_native_inline_video_method_with_caption(self):
        video_path = Path(self.tempdir.name) / "official_video.mp4"
        video_path.write_bytes(b"fake-video-for-adapter-test")

        self.publisher.send_media(video_path, "video", "A video caption")

        method, payload = self.fake_bot.calls[-1]
        self.assertEqual(method, "send_video")
        self.assertEqual(payload["chat_id"], "test-channel")
        self.assertEqual(payload["path"], str(video_path))
        self.assertEqual(payload["text"], "A video caption")


if __name__ == "__main__":
    unittest.main()
