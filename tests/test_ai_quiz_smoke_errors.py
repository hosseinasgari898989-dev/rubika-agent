import json
import unittest

from agent.brain import ai_quiz_smoke as smoke


class ErrorDetailTests(unittest.TestCase):
    def test_extracts_safe_fields_and_redacts_the_secret(self):
        payload = {
            "error": {
                "type": "invalid_request_error", "code": "json_validate_failed", "param": "response_format",
                "message": "bad key secret-key-value in   request\nbody",
                "metadata": {"provider_name": "Some Provider"},
            },
            "_retry_after": "30",
        }
        detail = smoke._error_detail(payload, "secret-key-value")
        self.assertEqual(detail["type"], "invalid_request_error")
        self.assertEqual(detail["code"], "json_validate_failed")
        self.assertEqual(detail["param"], "response_format")
        self.assertEqual(detail["retry_after"], "30")
        self.assertEqual(detail["upstream_provider"], "Some Provider")
        self.assertIn("<redacted>", detail["message"])
        self.assertNotIn("\n", detail["message"])
        self.assertNotIn("secret-key-value", json.dumps(detail))

    def test_empty_or_non_dict_payloads_return_none(self):
        for payload in (None, [], {}, {"choices": []}):
            self.assertIsNone(smoke._error_detail(payload, "secret-key-value"))

    def test_message_is_truncated_and_string_errors_are_redacted(self):
        long_detail = smoke._error_detail({"error": {"message": "x" * 500}}, "k")
        self.assertLessEqual(len(long_detail["message"]), 200)
        text_detail = smoke._error_detail({"error": "limit secret-key-value"}, "secret-key-value")
        self.assertNotIn("secret-key-value", json.dumps(text_detail))


if __name__ == "__main__":
    unittest.main()
