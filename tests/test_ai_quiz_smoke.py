import json
import unittest
from unittest.mock import patch

from agent.brain import ai_quiz_smoke as smoke
from agent.brain.ai_provider_clients import ProviderConfig


GROQ_KEY = "g" * 32
OPENROUTER_KEY = "o" * 32
QUIZ = {
    "question": "در فصل نخست، کدام شخصیت با نام مستعار مشخصی شناخته می‌شود؟",
    "options": ["گزینهٔ اول", "گزینهٔ دوم", "گزینهٔ سوم"],
    "answer_index": 1,
}
OK = {
    "choices": [{"message": {"content": json.dumps(QUIZ, ensure_ascii=False)}}],
    "usage": {"prompt_tokens": 25, "completion_tokens": 36, "total_tokens": 61},
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        return self.raw[:size]


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


class AIQuizSmokeTests(unittest.TestCase):
    def setUp(self):
        self.environ = {
            "GROQ_API_KEY": GROQ_KEY,
            "OPENROUTER_API_KEY": OPENROUTER_KEY,
            "GROQ_MODEL": "openai/gpt-oss-20b",
            "OPENROUTER_MODEL": smoke.OPENROUTER_FREE_MODEL,
        }

    def test_calls_groq_first_then_openrouter_free_and_reports_only_metadata(self):
        calls = []

        def fake_request(config, *, timeout=smoke.TIMEOUT_SECONDS, **kwargs):
            calls.append((config.provider, config.model))
            return 200, OK, None

        with patch.object(smoke, "_request", side_effect=fake_request):
            report = smoke.run_smoke(self.environ)

        self.assertTrue(report["ok"])
        self.assertEqual([row[0] for row in calls], ["groq", "openrouter"])
        self.assertEqual(calls[1][1], smoke.OPENROUTER_FREE_MODEL)
        self.assertEqual([row["http_status"] for row in report["providers"]], [200, 200])
        self.assertTrue(all(row["quiz_json_ok"] for row in report["providers"]))
        self.assertEqual(report["providers"][0]["tokens"]["total_tokens"], 61)
        encoded = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(GROQ_KEY, encoded)
        self.assertNotIn(OPENROUTER_KEY, encoded)
        self.assertNotIn(QUIZ["question"], encoded)

    def test_groq_failure_does_not_prevent_openrouter_attempt(self):
        calls = []

        def fake_request(config, *, timeout=smoke.TIMEOUT_SECONDS, **kwargs):
            calls.append(config.provider)
            if config.provider == "groq":
                return 429, {"error": {"message": "private key " + GROQ_KEY}}, "http_429"
            return 200, OK, None

        with patch.object(smoke, "_request", side_effect=fake_request):
            report = smoke.run_smoke(self.environ)

        self.assertEqual(calls, ["groq", "openrouter"])
        self.assertEqual(report["providers"][0]["http_status"], 429)
        self.assertEqual(report["providers"][0]["error"], "http_429")
        self.assertTrue(report["providers"][1]["quiz_json_ok"])
        self.assertNotIn(GROQ_KEY, json.dumps(report))

    def test_missing_groq_key_is_reported_and_openrouter_still_runs(self):
        env = dict(self.environ)
        env.pop("GROQ_API_KEY")
        calls = []

        def fake_request(config, *, timeout=smoke.TIMEOUT_SECONDS, **kwargs):
            calls.append(config.provider)
            return 200, OK, None

        with patch.object(smoke, "_request", side_effect=fake_request):
            report = smoke.run_smoke(env)

        self.assertEqual(calls, ["openrouter"])
        self.assertEqual(report["providers"][0]["error"], "groq_api_key_missing")
        self.assertTrue(report["providers"][1]["quiz_json_ok"])

    def test_openrouter_model_without_free_suffix_is_blocked(self):
        env = dict(self.environ)
        env["OPENROUTER_MODEL"] = "openai/gpt-4o-mini"
        calls = []

        def fake_request(config, *, timeout=smoke.TIMEOUT_SECONDS, **kwargs):
            calls.append(config.provider)
            return 200, OK, None

        with patch.object(smoke, "_request", side_effect=fake_request):
            report = smoke.run_smoke(env)

        self.assertEqual(calls, ["groq"])
        self.assertEqual(report["providers"][1]["error"], "openrouter_model_not_free")
        self.assertEqual(report["providers"][1]["model"], "openai/gpt-4o-mini")

    def test_quiz_schema_rejects_invalid_answer_index_and_option_count(self):
        invalid = {
            "question": "سؤال",
            "options": ["الف", "ب"],
            "answer_index": True,
        }
        payload = {
            "choices": [{"message": {"content": json.dumps(invalid, ensure_ascii=False)}}],
        }
        self.assertFalse(smoke._quiz_json_ok(payload))
        self.assertTrue(smoke._quiz_json_ok(OK))

    def test_http_request_uses_the_existing_provider_configuration_without_leaking_key(self):
        config = ProviderConfig(
            "openrouter", OPENROUTER_KEY, smoke.OPENROUTER_FREE_MODEL,
            "https://openrouter.ai/api/v1/chat/completions",
        )
        opener = FakeOpener(FakeResponse(OK))
        with patch.object(smoke.urllib.request, "build_opener", return_value=opener):
            status, payload, error = smoke._request(config)

        self.assertEqual(status, 200)
        self.assertIsNone(error)
        self.assertEqual(payload["usage"]["total_tokens"], 61)
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer " + OPENROUTER_KEY)
        self.assertNotIn(OPENROUTER_KEY, opener.request.full_url)
        body = json.loads(opener.request.data.decode("utf-8"))
        self.assertEqual(body["model"], smoke.OPENROUTER_FREE_MODEL)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(opener.timeout, smoke.TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
