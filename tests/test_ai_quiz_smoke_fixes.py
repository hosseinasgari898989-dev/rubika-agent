import json
import unittest
from unittest.mock import patch

from agent.brain import ai_quiz_smoke as smoke


GROQ_KEY = "g" * 32
OPENROUTER_KEY = "o" * 32
QUIZ = {
    "question": "در فصل نخست کدام شخصیت معرفی می‌شود؟",
    "options": ["گزینهٔ اول", "گزینهٔ دوم", "گزینهٔ سوم"],
    "answer_index": 1,
}
FENCED_QUIZ = "\x60\x60\x60json\n" + json.dumps(QUIZ, ensure_ascii=False) + "\n\x60\x60\x60"
VALID_PAYLOAD = {
    "choices": [{"message": {"content": FENCED_QUIZ}}],
    "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
}


class AIQuizSmokeFixTests(unittest.TestCase):
    def setUp(self):
        self.environ = {
            "GROQ_API_KEY": GROQ_KEY,
            "OPENROUTER_API_KEY": OPENROUTER_KEY,
            "GROQ_MODEL": "openai/gpt-oss-20b",
            "OPENROUTER_MODEL": "openrouter/free",
        }

    def test_extract_json_object_handles_fences_prose_and_no_braces(self):
        expected = json.dumps(QUIZ, ensure_ascii=False)
        fenced = "\x60\x60\x60json\n" + expected + "\n\x60\x60\x60"
        self.assertEqual(smoke._extract_json_object(fenced), expected)
        self.assertEqual(smoke._extract_json_object("مقدمه " + expected + " پایان"), expected)
        unchanged = "پاسخی بدون آکولاد"
        self.assertEqual(smoke._extract_json_object(unchanged), unchanged)

    def test_quiz_json_ok_accepts_fenced_valid_quiz(self):
        self.assertTrue(smoke._quiz_json_ok(VALID_PAYLOAD))

    def test_groq_retries_without_json_mode_after_http_400(self):
        calls = []

        def fake_request(config, *, json_mode=True, **kwargs):
            calls.append(json_mode)
            if json_mode is not False:
                return 400, {"error": {"code": "json_validate_failed"}}, "http_400"
            return 200, VALID_PAYLOAD, None

        with patch.object(smoke, "_request", side_effect=fake_request):
            result = smoke._provider_result("groq", self.environ)

        self.assertEqual(calls, [True, False])
        self.assertTrue(result["quiz_json_ok"])
        self.assertTrue(result["retried_without_json_mode"])
        self.assertEqual(result["http_status"], 200)


if __name__ == "__main__":
    unittest.main()
