import json
import unittest

from agent.rubika_preflight import verify_connection


class FakeBot:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def get_chat(self, chat_id):
        return {"status": "OK", "chat": {"chat_id": chat_id}}


class RubikaPreflightTests(unittest.TestCase):
    def test_success_does_not_publish(self):
        result = verify_connection("not-a-real-token-for-unit-test", "test-channel", FakeBot)
        self.assertTrue(result["ok"])
        self.assertTrue(result["bot_authenticated"])
        self.assertTrue(result["channel_access"])
        self.assertFalse(result["publishing"])

    def test_missing_token_fails_before_client_creation(self):
        result = verify_connection("", "test-channel", FakeBot)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "missing_RUBIKA_TOKEN")

    def test_rejected_channel_is_reported_without_raw_response(self):
        class RejectingBot:
            def __init__(self, **kwargs):
                pass

            def get_chat(self, chat_id):
                return {"status": "ERROR_CHAT_NOT_FOUND", "private": "do not print"}

        result = verify_connection("test-token", "bad-channel", RejectingBot)
        rendered = json.dumps(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "rubika_rejected_channel")
        self.assertNotIn("do not print", rendered)
        self.assertNotIn("test-token", rendered)

    def test_auth_exception_does_not_leak_exception_text(self):
        secret = "example-secret-token"

        def broken_factory(**kwargs):
            raise RuntimeError("Rejected token: " + secret)

        result = verify_connection(secret, "test-channel", broken_factory)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "RuntimeError")
        self.assertNotIn(secret, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
