from __future__ import annotations

import builtins
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain import main_channel_preflight, test_channel


ALLOWED_FAILURE_STAGES = {
    "missing_credentials",
    "rubka_import_failed",
    "bot_identity_failed",
    "bot_status_not_ok",
    "test_pin_match",
    "main_chat_failed",
    "none",
}


class FakeBot:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.identity_calls = 0
        self.chat_calls: list[str] = []

    def get_me(self):
        self.identity_calls += 1
        return {"status": "OK", "user": {"username": "must-not-be-logged"}}

    def get_chat(self, chat_id):
        self.chat_calls.append(chat_id)
        return {"status": "OK", "chat": {"chat_id": "must-not-be-logged"}}

    def send_message(self, *_args, **_kwargs):
        raise AssertionError("preflight must never send a message")


class MainChannelPreflightTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(
            test_channel,
            "is_test_channel_guid",
            side_effect=lambda value: str(value or "").strip() == "test-guid",
        )
        guard.start()
        self.addCleanup(guard.stop)

    def assert_safe_no_send_result(self, result):
        self.assertFalse(result["message_sent"])
        self.assertFalse(result["published"])
        self.assertIn(result["failure_stage"], ALLOWED_FAILURE_STAGES)
        encoded = json.dumps(result)
        self.assertNotIn("dummy-token", encoded)
        self.assertNotIn("sensitive-token-value", encoded)
        self.assertNotIn("must-not-be-logged", encoded)
        self.assertNotIn("main-guid", encoded)
        self.assertNotIn("private exception", encoded)

    def test_success_checks_identity_and_main_destination_without_sending(self):
        bot = FakeBot()
        result = main_channel_preflight.verify_main_channel(
            "dummy-token", "main-guid", lambda **kwargs: bot
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["bot_identity_ok"])
        self.assertTrue(result["main_destination_accessible"])
        self.assertFalse(result["main_matches_test_pin"])
        self.assertEqual(result["failure_stage"], "none")
        self.assertEqual(bot.identity_calls, 1)
        self.assertEqual(bot.chat_calls, ["main-guid"])
        self.assertEqual(
            result["main_guid_sha256_prefix"],
            test_channel.channel_guid_sha256("main-guid")[:12],
        )
        self.assert_safe_no_send_result(result)

    def test_missing_credentials_fail_before_client_creation(self):
        created = []
        result = main_channel_preflight.verify_main_channel(
            "", "main-guid", lambda **kwargs: created.append(kwargs)
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["token_present"])
        self.assertEqual(result["failure_stage"], "missing_credentials")
        self.assertEqual(created, [])
        self.assert_safe_no_send_result(result)

        result = main_channel_preflight.verify_main_channel(
            "dummy-token", "", lambda **kwargs: created.append(kwargs)
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["main_guid_present"])
        self.assertEqual(result["failure_stage"], "missing_credentials")
        self.assertEqual(created, [])
        self.assert_safe_no_send_result(result)

    def test_rubka_import_failure_is_reported_without_exception_details(self):
        original_import = builtins.__import__

        def fail_rubka_import(name, *args, **kwargs):
            if name == "rubka":
                raise ImportError("private exception with sensitive-token-value")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fail_rubka_import):
            result = main_channel_preflight.verify_main_channel(
                "dummy-token", "main-guid"
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["failure_stage"], "rubka_import_failed")
        self.assert_safe_no_send_result(result)

    def test_bot_identity_exception_is_reported_without_exception_details(self):
        def broken_factory(**kwargs):
            raise RuntimeError("private exception with sensitive-token-value")

        result = main_channel_preflight.verify_main_channel(
            "sensitive-token-value", "main-guid", broken_factory
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["bot_identity_ok"])
        self.assertEqual(result["failure_stage"], "bot_identity_failed")
        self.assert_safe_no_send_result(result)

    def test_bot_status_not_ok_has_its_own_failure_stage(self):
        class RejectedIdentityBot(FakeBot):
            def get_me(self):
                self.identity_calls += 1
                return {"status": "ERROR", "description": "private exception"}

        bot = RejectedIdentityBot()
        result = main_channel_preflight.verify_main_channel(
            "dummy-token", "main-guid", lambda **kwargs: bot
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["bot_identity_ok"])
        self.assertEqual(result["failure_stage"], "bot_status_not_ok")
        self.assertEqual(bot.chat_calls, [])
        self.assert_safe_no_send_result(result)

    def test_test_pin_match_is_blocked_before_get_chat(self):
        bot = FakeBot()
        result = main_channel_preflight.verify_main_channel(
            "dummy-token", "test-guid", lambda **kwargs: bot
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["bot_identity_ok"])
        self.assertTrue(result["main_matches_test_pin"])
        self.assertFalse(result["main_destination_accessible"])
        self.assertEqual(result["failure_stage"], "test_pin_match")
        self.assertEqual(bot.identity_calls, 1)
        self.assertEqual(bot.chat_calls, [])
        self.assert_safe_no_send_result(result)

    def test_main_chat_exception_and_non_ok_response_are_sanitized(self):
        class BrokenChatBot(FakeBot):
            def get_chat(self, chat_id):
                self.chat_calls.append(chat_id)
                raise RuntimeError("private exception with sensitive-token-value")

        class RejectedChatBot(FakeBot):
            def get_chat(self, chat_id):
                self.chat_calls.append(chat_id)
                return {"status": "ERROR", "description": "private exception"}

        for bot in (BrokenChatBot(), RejectedChatBot()):
            with self.subTest(bot=type(bot).__name__):
                result = main_channel_preflight.verify_main_channel(
                    "dummy-token", "main-guid", lambda **kwargs: bot
                )
                self.assertFalse(result["ok"])
                self.assertTrue(result["bot_identity_ok"])
                self.assertFalse(result["main_destination_accessible"])
                self.assertEqual(result["failure_stage"], "main_chat_failed")
                self.assertEqual(bot.chat_calls, ["main-guid"])
                self.assert_safe_no_send_result(result)

    def test_workflow_install_line_matches_pinned_requirements_including_pillow(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github" / "workflows" / "rubika-main-channel-preflight.yml"
        ).read_text(encoding="utf-8")
        expected = (
            'run: python -m pip install --disable-pip-version-check '
            '"rubka==8.1.7" "Pillow>=10,<13"'
        )
        install_lines = [
            line.strip()
            for line in workflow.splitlines()
            if "Install the pinned Rubka client" in line
            or "rubka==8.1.7" in line
        ]
        self.assertEqual(
            install_lines,
            ["- name: Install the pinned Rubka client", expected],
        )

    def test_response_payload_rejects_non_dict_json(self):
        self.assertEqual(main_channel_preflight._response_payload("[]"), {})
        self.assertEqual(main_channel_preflight._response_payload("not-json"), {})


if __name__ == "__main__":
    unittest.main()
