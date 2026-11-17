from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from agent.brain import main_channel_preflight, test_channel


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

    def test_success_checks_identity_and_main_destination_without_sending(self):
        bot = FakeBot()
        result = main_channel_preflight.verify_main_channel(
            "dummy-token", "main-guid", lambda **kwargs: bot
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["bot_identity_ok"])
        self.assertTrue(result["main_destination_accessible"])
        self.assertFalse(result["main_matches_test_pin"])
        self.assertFalse(result["message_sent"])
        self.assertFalse(result["published"])
        self.assertEqual(bot.identity_calls, 1)
        self.assertEqual(bot.chat_calls, ["main-guid"])
        self.assertEqual(result["main_guid_sha256_prefix"], test_channel.channel_guid_sha256("main-guid")[:12])
        self.assertNotIn("dummy-token", json.dumps(result))
        self.assertNotIn("must-not-be-logged", json.dumps(result))
        self.assertNotIn("main-guid", json.dumps(result))

    def test_test_pin_match_is_blocked_after_identity_and_before_get_chat(self):
        bot = FakeBot()
        result = main_channel_preflight.verify_main_channel(
            "dummy-token", "test-guid", lambda **kwargs: bot
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["bot_identity_ok"])
        self.assertTrue(result["main_matches_test_pin"])
        self.assertFalse(result["main_destination_accessible"])
        self.assertEqual(bot.identity_calls, 1)
        self.assertEqual(bot.chat_calls, [])

    def test_missing_credentials_fail_before_client_creation(self):
        created = []
        result = main_channel_preflight.verify_main_channel(
            "", "main-guid", lambda **kwargs: created.append(kwargs)
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["token_present"])
        self.assertEqual(created, [])

        result = main_channel_preflight.verify_main_channel(
            "dummy-token", "", lambda **kwargs: created.append(kwargs)
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["main_guid_present"])
        self.assertEqual(created, [])

    def test_auth_failure_does_not_expose_exception_text_or_secret(self):
        secret = "sensitive-token-value"

        def broken_factory(**kwargs):
            raise RuntimeError("rejected " + secret)

        result = main_channel_preflight.verify_main_channel(
            secret, "main-guid", broken_factory
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["bot_identity_ok"])
        rendered = json.dumps(result)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("rejected", rendered)


if __name__ == "__main__":
    unittest.main()
