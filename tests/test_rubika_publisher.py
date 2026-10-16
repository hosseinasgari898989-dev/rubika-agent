from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.brain.rubika_publisher import RubikaPublisher, _decorate_official_mob_caption


class FakeBot:
    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.calls = []
        self.uploads = []
        self.posts = []

    def send_image(self, **kwargs):
        self.calls.append(("send_image", kwargs))
        return {"status": "OK"}

    def send_video(self, **kwargs):
        self.calls.append(("send_video", kwargs))
        return {"status": "OK"}

    def send_file(self, **kwargs):
        self.calls.append(("send_file", kwargs))
        return {"status": "OK"}

    def get_upload_url(self, media_type):
        self.uploads.append(("get_upload_url", media_type))
        return "https://upload.example.invalid/media"

    def upload_media_file(self, upload_url, name, path):
        self.uploads.append(("upload_media_file", upload_url, name, Path(path).read_bytes()))
        return "uploaded-file-id"

    def _post(self, method, payload):
        self.posts.append((method, payload))
        return {"status": "OK", "data": {"message_id": "formatted-media-message"}}


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

    def test_official_mob_caption_gets_bold_header_and_quoted_source_without_markup_injection(self):
        caption = (
            "🎮 ماب انترتینمنت پستی منتشر کرد و گفت:\\n\\n"
            "Official **source** text with ||spoiler|| markers and a second line.\\n\\n"
            "📝 ترجمه فارسی:\\nA Persian translation."
        )
        video_path = Path(self.tempdir.name) / "mob-news.mp4"
        video_path.write_bytes(b"fake-mob-news-video")
        outgoing = _decorate_official_mob_caption(caption)
        self.assertIn("**🎮 ماب انترتینمنت پستی منتشر کرد و گفت:**", outgoing)
        self.assertIn("> Official ", outgoing)
        self.assertNotIn("**source**", outgoing)
        self.assertNotIn("||spoiler||", outgoing)

        self.publisher.send_media(video_path, "video", caption)
        payload = self.fake_bot.posts[0][1]
        types = [part["type"] for part in payload["metadata"]["meta_data_parts"]]
        self.assertGreaterEqual(types.count("Bold"), 2)
        self.assertIn("Quote", types)
        self.assertNotIn("**", payload["text"])
        self.assertNotIn("||", payload["text"])
        self.assertIn("Official", payload["text"])

    def test_formatted_video_caption_uploads_once_and_sends_bold_quote_and_spoiler_metadata(self):
        video_path = Path(self.tempdir.name) / "formatted.mp4"
        video_path.write_bytes(b"fake-video-for-formatting-test")
        caption = "**Bold title**\n> quoted announcement\n||spoiler text||"

        result = self.publisher.send_media(video_path, "video", caption)

        self.assertEqual(result["status"], "OK")
        self.assertEqual(self.fake_bot.uploads[0][0], "get_upload_url")
        self.assertEqual(self.fake_bot.uploads[0][1], "Video")
        self.assertEqual(self.fake_bot.uploads[1][0], "upload_media_file")
        self.assertEqual(len(self.fake_bot.posts), 1)
        method, payload = self.fake_bot.posts[0]
        self.assertEqual(method, "sendFile")
        self.assertEqual(payload["chat_id"], "test-channel")
        self.assertEqual(payload["file_id"], "uploaded-file-id")
        self.assertNotIn("**", payload["text"])
        self.assertNotIn("||", payload["text"])
        kinds = {part["type"] for part in payload["metadata"]["meta_data_parts"]}
        self.assertTrue({"Bold", "Quote", "Spoiler"}.issubset(kinds))
        self.assertEqual(self.fake_bot.calls, [])

    def test_formatted_image_uses_image_upload_type_and_metadata_payload(self):
        caption = "**Official update**\n> Verified source quote"
        self.publisher.send_media(self.media_path, "image", caption)

        self.assertEqual(self.fake_bot.uploads[0], ("get_upload_url", "Image"))
        self.assertEqual(self.fake_bot.posts[0][0], "sendFile")
        types = {part["type"] for part in self.fake_bot.posts[0][1]["metadata"]["meta_data_parts"]}
        self.assertTrue({"Bold", "Quote"}.issubset(types))
        self.assertEqual(self.fake_bot.calls, [])

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
