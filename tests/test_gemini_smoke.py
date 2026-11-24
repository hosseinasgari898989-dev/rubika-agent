import json
import unittest
from unittest.mock import patch

from agent.brain import gemini_smoke as smoke

MODELS = {"models": [
    {"name": "models/gemini-x-pro", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-x-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-x-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-x-flash-image", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/embedding-flash", "supportedGenerationMethods": ["embedContent"]},
]}
OK = {"candidates": [{"content": {"parts": [{"text": "OK"}]}}],
      "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1, "totalTokenCount": 6}}


class GeminiSmokeTests(unittest.TestCase):
    def test_prefers_flash_lite_and_never_leaks_the_key(self):
        def fake(method, path, key, body=None, timeout=30):
            return (200, MODELS) if method == "GET" else (200, OK)
        with patch.object(smoke, "_request", side_effect=fake), patch.dict("os.environ", {"GEMINI_API_KEY": "secret-key-value"}):
            report = smoke.run_smoke()
        encoded = json.dumps(report)
        self.assertTrue(report["ok"])
        self.assertEqual(report["first_working_model"], "gemini-x-flash-lite")
        self.assertEqual(report["models_listed"], 2)
        self.assertNotIn("secret-key-value", encoded)
        self.assertNotIn("gemini-x-pro", encoded)

    def test_missing_key_makes_no_call(self):
        with patch.object(smoke, "_request") as request, patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            report = smoke.run_smoke()
        self.assertFalse(report["ok"])
        self.assertFalse(report["key_present"])
        request.assert_not_called()

    def test_rate_limit_is_reported_not_raised(self):
        def fake(method, path, key, body=None, timeout=30):
            if method == "GET":
                return 200, MODELS
            return 429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota secret-key-value"}}
        with patch.object(smoke, "_request", side_effect=fake), patch.dict("os.environ", {"GEMINI_API_KEY": "secret-key-value"}):
            report = smoke.run_smoke()
        self.assertFalse(report["ok"])
        self.assertEqual(report["tested"][0]["status"], 429)
        self.assertNotIn("secret-key-value", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
