from __future__ import annotations

import builtins
import json
import re
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
EXPECTED_METHODS = {
    "get_chat",
    "get_chat_info",
    "get_channel_info",
    "get_chat_admins",
    "get_admin_chats",
    "get_chat_member",
    "get_chats",
}


class FakeBot:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.identity_calls = 0
        self.chat_calls: list[str] = []
        self.send_calls = 0

    def get_me(self):
        self.identity_calls += 1
        return {"status": "OK", "user": {"username": "must-not-be-logged"}}

    def get_chat(self, chat_id):
        self.chat_calls.append(chat_id)
        return {"status": "OK", "chat": {"chat_id": "must-not-be-logged"}}

    def send_message(self, *_args, **_kwargs):
        self.send_calls += 1

    def send_file(self, *_args, **_kwargs):
        self.send_calls += 1

    def sendMessage(self, *_args, **_kwargs):
        self.send_calls += 1


class MainChannelPreflightTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(
            test_channel,
            "is_test_channel_guid",
            side_effect=lambda value: str(value or "").strip() == "test-guid",
        )
        guard.start()
        self.addCleanup(guard.stop)

    def assert_safe_no_send_result(self, result, *sensitive_values):
        self.assertFalse(result["message_sent"])
        self.assertFalse(result["published"])
        self.assertIn(result["failure_stage"], ALLOWED_FAILURE_STAGES)
        encoded = json.dumps(result, sort_keys=True)
        for sensitive in (
            "dummy-token",
            "sensitive-token-value",
            "must-not-be-logged",
            "main-guid",
            *sensitive_values,
        ):
            if sensitive:
                self.assertNotIn(sensitive, encoded)

    def test_success_checks_identity_and_main_destination_without_sending(self):
        bot = FakeBot()
        result = main_channel_preflight.verify_main_channel(
            "dummy-token", "main-guid", lambda **kwargs: bot
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["bot_identity_ok"])
        self.assertTrue(result["main_destination_accessible"])
        self.assertEqual(result["accessible_via"], "get_chat")
        self.assertFalse(result["main_matches_test_pin"])
        self.assertEqual(result["failure_stage"], "none")
        self.assertEqual(bot.identity_calls, 1)
        self.assertEqual(bot.chat_calls, ["main-guid"])
        self.assertEqual(bot.send_calls, 0)
        self.assertEqual(set(result["read_only_method_presence"]), EXPECTED_METHODS)
        self.assertTrue(result["read_only_method_presence"]["get_chat"])
        self.assertFalse(result["read_only_method_presence"]["get_chat_info"])
        self.assertEqual(
            result["read_only_checks"],
            [{"method": "get_chat", "ok": True, "status": "OK"}],
        )
        self.assertEqual(
            result["main_guid_sha256_prefix"],
            test_channel.channel_guid_sha256("main-guid")[:12],
        )
        self.assert_safe_no_send_result(result)

    def test_get_chat_fails_but_get_chat_info_confirms_destination(self):
        class FallbackBot(FakeBot):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.chat_info_calls: list[str] = []

            def get_chat(self, chat_id):
                self.chat_calls.append(chat_id)
                raise RuntimeError("private exception with dummy-token and main-guid")

            def get_chat_info(self, chat_id):
                self.chat_info_calls.append(chat_id)
                return {"status": "OK", "chat": {"id": "not-for-output"}}

        bot = FallbackBot()
        result = main_channel_preflight.verify_main_channel(
            "dummy-token", "main-guid", lambda **kwargs: bot
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["main_destination_accessible"])
        self.assertEqual(result["accessible_via"], "get_chat_info")
        self.assertEqual(result["failure_stage"], "none")
        self.assertEqual(bot.chat_calls, ["main-guid"])
        self.assertEqual(bot.chat_info_calls, ["main-guid"])
        self.assertEqual(result["chat_exception_type"], "RuntimeError")
        self.assertEqual(
            result["read_only_checks"],
            [
                {"method": "get_chat", "ok": False, "exception_type": "RuntimeError"},
                {"method": "get_chat_info", "ok": True, "status": "OK"},
            ],
        )
        self.assertEqual(bot.send_calls, 0)
        self.assert_safe_no_send_result(result)

    def test_no_read_only_candidate_confirms_destination(self):
        class NoSuccessBot(FakeBot):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.chat_info_calls: list[str] = []
                self.chat_member_calls = 0
                self.admin_chats_calls = 0

            def get_chat(self, chat_id):
                self.chat_calls.append(chat_id)
                return {"status": "ERROR", "description": "not accessible"}

            def get_chat_info(self, chat_id):
                self.chat_info_calls.append(chat_id)
                return {"status": "DENIED"}

            def get_chat_member(self, chat_id, user_id):
                self.chat_member_calls += 1
                raise AssertionError("must not invent a member id")

            def get_admin_chats(self):
                self.admin_chats_calls += 1
                raise AssertionError("does not accept a chat id")

        bot = NoSuccessBot()
        result = main_channel_preflight.verify_main_channel(
            "dummy-token", "main-guid", lambda **kwargs: bot
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["main_destination_accessible"])
        self.assertIsNone(result["accessible_via"])
        self.assertEqual(result["failure_stage"], "main_chat_failed")
        self.assertEqual(bot.chat_calls, ["main-guid"])
        self.assertEqual(bot.chat_info_calls, ["main-guid"])
        self.assertEqual(bot.chat_member_calls, 0)
        self.assertEqual(bot.admin_chats_calls, 0)
        self.assertEqual(result["chat_response_status"], "ERROR")
        self.assertEqual(result["chat_response_description"], "not accessible")
        self.assertEqual(bot.send_calls, 0)
        self.assert_safe_no_send_result(result)

    def test_long_odd_response_description_is_truncated_and_sanitized(self):
        raw_guid = "c0ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        raw_token = "987654321:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

        class OddDescriptionBot(FakeBot):
            def get_chat(self, chat_id):
                self.chat_calls.append(chat_id)
                return {
                    "status": "ERROR",
                    "description": (
                        f"Denied {raw_guid} token={raw_token} {raw_guid} "
                        "odd<unicode>message " + ("x" * 70)
                    ),
                }

        bot = OddDescriptionBot()
        result = main_channel_preflight.verify_main_channel(
            "dummy-token", raw_guid, lambda **kwargs: bot
        )
        description = result["chat_response_description"]
        self.assertIsNotNone(description)
        self.assertLessEqual(len(description), 40)
        self.assertRegex(description, re.compile(r"^[A-Za-z0-9 _.,:;!?()/+-]*$"))
        self.assertNotIn(raw_guid, description)
        self.assertNotIn(raw_token, description)
        self.assertEqual(result["chat_response_status"], "ERROR")
        self.assert_safe_no_send_result(result, raw_guid, raw_token)
        self.assertEqual(bot.send_calls, 0)

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

    def test_test_pin_match_is_blocked_before_any_chat_method(self):
        bot = FakeBot()
        result = main_channel_preflight.verify_main_channel(
            "dummy-token", "test-guid", lambda **kwargs: bot
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["bot_identity_ok"])
        self.assertTrue(result["main_matches_test_pin"])
        self.assertFalse(result["main_destination_accessible"])
        self.assertIsNone(result["accessible_via"])
        self.assertEqual(result["failure_stage"], "test_pin_match")
        self.assertEqual(bot.identity_calls, 1)
        self.assertEqual(bot.chat_calls, [])
        self.assertEqual(bot.send_calls, 0)
        self.assertEqual(result["read_only_checks"], [])
        self.assert_safe_no_send_result(result)

    def test_installed_rubka_bot_read_only_method_inventory_uses_hasattr(self):
        try:
            from rubka import Bot
        except ImportError:
            self.skipTest("Rubka is installed in the dedicated workflow job")

        bot = Bot(token="unit-test-placeholder", timeout=1, show_progress=False)
        inventory = {
            name: bool(hasattr(bot, name))
            for name in main_channel_preflight.READ_ONLY_CHAT_METHODS
        }
        self.assertEqual(set(inventory), EXPECTED_METHODS)
        self.assertTrue(all(type(value) is bool for value in inventory.values()))
        print(json.dumps(inventory, sort_keys=True))

    def test_workflow_installs_pinned_client_and_runs_read_only_tests(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github" / "workflows" / "rubika-main-channel-preflight.yml"
        ).read_text(encoding="utf-8")
        expected_install = (
            'run: python -m pip install --disable-pip-version-check '
            '"rubka==8.1.7" "Pillow>=10,<13"'
        )
        install_lines = [
            line.strip()
            for line in workflow.splitlines()
            if "rubka==8.1.7" in line
        ]
        self.assertEqual(install_lines, [expected_install, expected_install])
        self.assertIn('      - "tests/test_main_channel_preflight.py"', workflow)
        self.assertIn(
            "python -m unittest discover -s tests -p 'test_main_channel_preflight.py' -v",
            workflow,
        )
        self.assertIn("if: github.event_name != 'pull_request'", workflow)

    def test_response_payload_rejects_non_dict_json(self):
        self.assertEqual(main_channel_preflight._response_payload("[]"), {})
        self.assertEqual(main_channel_preflight._response_payload("not-json"), {})


if __name__ == "__main__":
    unittest.main()
