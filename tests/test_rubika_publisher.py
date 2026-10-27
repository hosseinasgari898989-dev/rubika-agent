from __future__ import annotations

from agent.brain.edit_caption import EDIT_CAPTION

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain import test_channel
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


TEST_CHANNEL_FOR_TESTS = "synthetic-test-channel-guid"


class RubikaPublisherMediaTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(
            test_channel, "is_test_channel_guid",
            side_effect=lambda value: str(value or "").strip() == TEST_CHANNEL_FOR_TESTS,
        )
        guard.start()
        self.addCleanup(guard.stop)
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.media_path = Path(self.tempdir.name) / "official_media.webp"
        self.media_path.write_bytes(b"fake-media-for-adapter-test")
        self.fake_bot = FakeBot()
        self.publisher = RubikaPublisher(
            "test-token",
            TEST_CHANNEL_FOR_TESTS,
            bot_factory=lambda **kwargs: self.fake_bot,
        )

    def test_official_mob_caption_bolds_only_translation_and_escapes_source_markup(self):
        caption = (
            "🎮 ماب انترتینمنت پستی منتشر کرد و گفت:\n\n"
            "Official **source** text with ||spoiler|| markers and a second line.\n\n"
            "📝 ترجمه فارسی:\nA Persian translation."
        )
        video_path = Path(self.tempdir.name) / "mob-news.mp4"
        video_path.write_bytes(b"fake-mob-news-video")
        outgoing = _decorate_official_mob_caption(caption)

        self.assertTrue(outgoing.startswith("🎮 ماب انترتینمنت پستی منتشر کرد و گفت:\n\n"))
        self.assertIn("Official", outgoing)
        self.assertNotIn("> Official", outgoing)
        self.assertNotIn("**🎮", outgoing)
        self.assertNotIn("**📝 ترجمه فارسی:**", outgoing)
        self.assertIn("**A Persian translation.**", outgoing)
        self.assertNotIn("**source**", outgoing)
        self.assertNotIn("||spoiler||", outgoing)

        self.publisher.send_media(video_path, "video", caption)
        payload = self.fake_bot.posts[0][1]
        parts = payload["metadata"]["meta_data_parts"]
        bold_parts = [part for part in parts if part["type"] == "Bold"]
        self.assertEqual(len(bold_parts), 1)
        translated = "A Persian translation."
        expected_offset = len(payload["text"].split(translated)[0].encode("utf-16-be")) // 2
        self.assertEqual(bold_parts[0]["from_index"], expected_offset)
        self.assertEqual(bold_parts[0]["length"], len(translated.encode("utf-16-be")) // 2)
        self.assertNotIn("Quote", [part["type"] for part in parts])
        self.assertNotIn("Spoiler", [part["type"] for part in parts])
        self.assertNotIn("**", payload["text"])
        self.assertNotIn("||", payload["text"])
        self.assertIn("Official", payload["text"])
        self.assertIn("📝 ترجمه فارسی:", payload["text"])

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
        self.assertEqual(payload["chat_id"], TEST_CHANNEL_FOR_TESTS)
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
        self.assertEqual(payload["chat_id"], TEST_CHANNEL_FOR_TESTS)
        self.assertEqual(payload["path"], str(self.media_path))
        self.assertEqual(payload["text"], "A test caption")
        self.assertEqual(payload["file_name"], "official_media.webp")

    def test_image_never_falls_back_to_generic_file_attachment(self):
        self.publisher.send_media(self.media_path, "image", "A test caption")
        self.assertNotIn("send_file", [method for method, _ in self.fake_bot.calls])

    def test_edit_caption_uses_plain_native_video_payload_without_bold_metadata(self):
        video_path = Path(self.tempdir.name) / "edit_video.mp4"
        video_path.write_bytes(b"fake-video-for-adapter-test")
        self.assertEqual(_decorate_official_mob_caption(EDIT_CAPTION), EDIT_CAPTION)
        self.publisher.send_media(video_path, "video", EDIT_CAPTION)
        self.assertFalse(self.fake_bot.posts)
        method, payload = self.fake_bot.calls[-1]
        self.assertEqual(method, "send_video")
        self.assertEqual(payload["text"], EDIT_CAPTION)
        self.assertNotIn("**", payload["text"])

    def test_video_uses_native_inline_video_method_with_caption(self):
        video_path = Path(self.tempdir.name) / "official_video.mp4"
        video_path.write_bytes(b"fake-video-for-adapter-test")

        self.publisher.send_media(video_path, "video", "A video caption")

        method, payload = self.fake_bot.calls[-1]
        self.assertEqual(method, "send_video")
        self.assertEqual(payload["chat_id"], TEST_CHANNEL_FOR_TESTS)
        self.assertEqual(payload["path"], str(video_path))
        self.assertEqual(payload["text"], "A video caption")


    def test_non_test_destination_is_rejected_before_any_upload_or_send(self):
        blocked = RubikaPublisher(
            "test-token",
            "not-the-test-channel",
            bot_factory=lambda **kwargs: self.fake_bot,
        )
        with self.assertRaisesRegex(ValueError, "rubika_destination_not_test_channel"):
            blocked.send_media(self.media_path, "image", "A test caption")
        self.assertEqual(self.fake_bot.uploads, [])
        self.assertEqual(self.fake_bot.calls, [])
        self.assertEqual(self.fake_bot.posts, [])


if __name__ == "__main__":
    unittest.main()
