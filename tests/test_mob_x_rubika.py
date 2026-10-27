from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.brain import test_channel
from agent.brain.mob_x_rubika import (
    owner_preview_keypad,
    send_owner_preview,
    send_test_channel_once,
    send_uncertain_notice,
    upload_preview_media,
)


class RubikaMediaMock:
    def __init__(self, **kwargs):
        self.token = kwargs.get("token")
        self.get_upload_url = Mock(return_value="https://upload.example.invalid")
        self.upload_media_file = Mock(return_value="rubika-file-001")
        self._post = Mock(return_value={"status": "OK", "data": {"message_id": "msg-123"}})


TEST_CHANNEL_FOR_TESTS = "synthetic-test-channel-guid"


class MobXRubikaTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(
            test_channel, "is_test_channel_guid",
            side_effect=lambda value: str(value or "").strip() == TEST_CHANNEL_FOR_TESTS,
        )
        guard.start()
        self.addCleanup(guard.stop)

    def test_owner_preview_keyboard_has_publish_edit_and_reject_only(self):
        keyboard = owner_preview_keypad("job_abcdef1234567890")
        buttons = keyboard["rows"][0]["buttons"]
        self.assertEqual(
            [item["id"] for item in buttons],
            [
                "p_mob_publish_job_abcdef1234567890",
                "p_mob_edit_job_abcdef1234567890",
                "p_mob_reject_job_abcdef1234567890",
            ],
        )
        with self.assertRaises(ValueError):
            owner_preview_keypad("not-a-job")

    def test_upload_is_not_a_send_and_returns_rubika_file_id(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "preview.jpg"
            path.write_bytes(b"mock image bytes")
            holder = {}
            def factory(**kwargs):
                holder["bot"] = RubikaMediaMock(**kwargs)
                return holder["bot"]
            file_id = upload_preview_media("test-token", path, "image", bot_factory=factory)
            self.assertEqual(file_id, "rubika-file-001")
            holder["bot"].get_upload_url.assert_called_once_with("Image")
            holder["bot"].upload_media_file.assert_called_once()
            holder["bot"]._post.assert_not_called()

    def test_preview_is_sent_to_owner_with_plain_caption_and_actions_once(self):
        holder = {}
        def factory(**kwargs):
            holder["bot"] = RubikaMediaMock(**kwargs)
            return holder["bot"]
        message_id = send_owner_preview(
            "test-token", "owner-chat", "rubika-file-001", "کپشن ساده", "job_abcdef1234567890",
            bot_factory=factory,
        )
        self.assertEqual(message_id, "msg-123")
        holder["bot"]._post.assert_called_once()
        method, payload = holder["bot"]._post.call_args.args
        self.assertEqual(method, "sendFile")
        self.assertEqual(payload["chat_id"], "owner-chat")
        self.assertEqual(payload["file_id"], "rubika-file-001")
        self.assertEqual(payload["text"], "کپشن ساده")
        self.assertIn("inline_keypad", payload)

    def test_formatted_mob_x_caption_sends_native_bold_and_quote_metadata(self):
        from agent.brain.mob_x_link_processor import build_mob_x_caption

        caption = build_mob_x_caption("Official Mob update", "ترجمهٔ فارسی")
        holder = {}

        def factory(**kwargs):
            holder["bot"] = RubikaMediaMock(**kwargs)
            return holder["bot"]

        send_test_channel_once(
            "test-token", TEST_CHANNEL_FOR_TESTS, "rubika-file-001", caption,
            bot_factory=factory,
        )
        _, payload = holder["bot"]._post.call_args.args
        self.assertNotIn("**", payload["text"])
        self.assertNotIn("$", payload["text"])
        self.assertIn("ترجمه متن:", payload["text"])
        kinds = {part["type"] for part in payload["metadata"]["meta_data_parts"]}
        self.assertTrue({"Bold", "Quote"}.issubset(kinds))
        quote_parts = [part for part in payload["metadata"]["meta_data_parts"] if part["type"] == "Quote"]
        self.assertEqual(len(quote_parts), 1)

    def test_channel_delivery_is_exactly_one_send_and_has_no_owner_buttons(self):
        holder = {}
        def factory(**kwargs):
            holder["bot"] = RubikaMediaMock(**kwargs)
            return holder["bot"]
        send_test_channel_once("test-token", TEST_CHANNEL_FOR_TESTS, "rubika-file-001", "کپشن ساده", bot_factory=factory)
        holder["bot"]._post.assert_called_once()
        _, payload = holder["bot"]._post.call_args.args
        self.assertEqual(payload["chat_id"], TEST_CHANNEL_FOR_TESTS)
        self.assertNotIn("inline_keypad", payload)

    def test_non_test_channel_is_rejected_before_bot_creation(self):
        holder = {}
        def factory(**kwargs):
            holder["bot"] = RubikaMediaMock(**kwargs)
            return holder["bot"]
        with self.assertRaisesRegex(RuntimeError, "rubika_destination_not_test_channel"):
            send_test_channel_once(
                "test-token", "different-channel", "rubika-file-001", "کپشن ساده",
                bot_factory=factory,
            )
        self.assertNotIn("bot", holder)

    def test_ambiguous_send_response_raises_without_retrying(self):
        holder = {}
        def factory(**kwargs):
            holder["bot"] = RubikaMediaMock(**kwargs)
            holder["bot"]._post.return_value = {"status": "ERROR", "data": {}}
            return holder["bot"]
        with self.assertRaisesRegex(RuntimeError, "rubika_send_file_response_invalid"):
            send_test_channel_once("test-token", TEST_CHANNEL_FOR_TESTS, "rubika-file-001", "caption", bot_factory=factory)
        holder["bot"]._post.assert_called_once()

    def test_uncertain_notice_has_manual_confirmation_not_retry(self):
        holder = {}
        def factory(**kwargs):
            holder["bot"] = RubikaMediaMock(**kwargs)
            return holder["bot"]
        send_uncertain_notice(
            "test-token", "owner-chat", "job_abcdef1234567890",
            "نتیجهٔ ارسال نامشخص است؛ ارسال خودکار تکرار نمی‌شود.",
            bot_factory=factory,
        )
        holder["bot"]._post.assert_called_once()
        method, payload = holder["bot"]._post.call_args.args
        self.assertEqual(method, "sendMessage")
        button = payload["inline_keypad"]["rows"][0]["buttons"][0]
        self.assertEqual(button["id"], "p_mob_manualconfirm_job_abcdef1234567890")
        self.assertNotIn("ارسال مجدد", button["button_text"])


if __name__ == "__main__":
    unittest.main()
