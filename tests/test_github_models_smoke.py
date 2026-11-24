import json
import unittest
from unittest.mock import patch

from agent.brain import github_models_smoke as smoke

CATALOG = [{"id": "openai/gpt-4.1-mini"}, {"id": "openai/gpt-4o"}, {"id": "other/model"}]
OK = {"choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}}
QUIZ = {"choices": [{"message": {"content": json.dumps({"question": "q", "options": ["a", "b", "c"], "answer_index": 1})}}]}


class GithubModelsSmokeTests(unittest.TestCase):
    def test_picks_preferred_model_runs_quiz_and_never_leaks_token(self):
        answers = [OK, QUIZ]

        def fake(method, url, token, body=None, timeout=45):
            if method == "GET":
                return 200, CATALOG
            return 200, answers.pop(0)

        with patch.object(smoke, "_request", side_effect=fake), patch.dict("os.environ", {"GITHUB_TOKEN": "secret-token-value"}):
            report = smoke.run_smoke()
        self.assertTrue(report["ok"])
        self.assertEqual(report["first_working_model"], "openai/gpt-4.1-mini")
        self.assertTrue(report["tested"][0]["quiz_json_ok"])
        self.assertNotIn("secret-token-value", json.dumps(report))

    def test_missing_token_makes_no_call(self):
        with patch.object(smoke, "_request") as request, patch.dict("os.environ", {"GITHUB_TOKEN": ""}):
            report = smoke.run_smoke()
        self.assertFalse(report["ok"])
        request.assert_not_called()

    def test_rate_limit_is_reported_not_raised(self):
        def fake(method, url, token, body=None, timeout=45):
            if method == "GET":
                return 200, CATALOG
            return 429, {"error": {"message": "limit secret-token-value"}}

        with patch.object(smoke, "_request", side_effect=fake), patch.dict("os.environ", {"GITHUB_TOKEN": "secret-token-value"}):
            report = smoke.run_smoke()
        self.assertFalse(report["ok"])
        self.assertEqual(report["tested"][0]["status"], 429)
        self.assertNotIn("secret-token-value", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
