from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import call, patch
from io import StringIO

from agent.brain import ai_health_check as health
from agent.brain.ai_provider_clients import ProviderClientError, ProviderConfig, ProviderResult


ROOT = Path(__file__).resolve().parents[1]


class AIHealthCheckTests(unittest.TestCase):
    def configs(self):
        return (
            ProviderConfig("groq", "g" * 32, "openai/gpt-oss-20b", "https://api.groq.com/openai/v1/chat/completions"),
            ProviderConfig("openrouter", "o" * 32, "openrouter/free", "https://openrouter.ai/api/v1/chat/completions"),
        )

    def test_rate_limit_isolated_and_safe_fields_are_reported(self):
        groq, openrouter = self.configs()
        private_response = "PRIVATE FULL PROVIDER RESPONSE MUST NOT APPEAR"
        with (
            patch.object(health, "provider_config", side_effect=[groq, openrouter]) as config_mock,
            patch.object(
                health,
                "invoke_provider",
                side_effect=[
                    ProviderClientError(
                        "provider_http_429",
                        diagnostics={"http_status": 429, "provider_error_type": "rate_limit_error", "provider_error_code": "rate_limited", "provider_message": private_response},
                    ),
                    ProviderResult("openrouter", "openrouter/free", private_response, {}, 200, "stop"),
                ],
            ) as invoke_mock,
        ):
            results = tuple(row for provider in health.PROVIDERS for row in health.check_provider(provider))

        self.assertEqual([row.status for row in results], ["rate limit", "ok"])
        self.assertEqual(config_mock.call_args_list, [call("groq"), call("openrouter")])
        self.assertEqual(invoke_mock.call_count, 2)
        self.assertEqual([used.kwargs["max_output_tokens"] for used in invoke_mock.call_args_list], [10, 10])
        self.assertEqual(results[0].http_status, 429)
        self.assertEqual(results[0].provider_error_type, "rate_limit_error")
        self.assertEqual(results[0].provider_error_code, "rate_limited")
        self.assertEqual(results[0].error_code, "http_429")
        self.assertEqual(results[0].response_content_non_empty, None)
        self.assertEqual(results[1].http_status, 200)
        self.assertTrue(results[1].response_content_non_empty)
        summary = health.format_summary(results)
        self.assertIn("openai/gpt-oss-20b", summary)
        self.assertIn("openrouter/free", summary)
        self.assertNotIn(private_response, summary)
        self.assertNotIn("g" * 32, summary)
        self.assertNotIn("o" * 32, summary)

    def test_empty_http_200_response_gets_one_64_token_followup(self):
        groq = self.configs()[0]
        with (
            patch.object(health, "provider_config", return_value=groq),
            patch.object(
                health,
                "invoke_provider",
                side_effect=[
                    ProviderClientError(
                        "provider_text_missing",
                        outcome="unknown",
                        diagnostics={"http_status": 200, "finish_reason": "length"},
                    ),
                    ProviderResult("groq", groq.model, "OK", {}, 200, "stop"),
                ],
            ) as invoke_mock,
        ):
            rows = health.check_provider("groq")

        self.assertEqual([row.status for row in rows], ["empty response", "ok"])
        self.assertEqual([row.max_output_tokens for row in rows], [10, 64])
        self.assertEqual(rows[0].http_status, 200)
        self.assertEqual(rows[0].exception_class, "ProviderClientError")
        self.assertEqual(rows[0].provider_error_code, "provider_text_missing")
        self.assertFalse(rows[0].response_content_non_empty)
        self.assertEqual(rows[0].finish_reason, "length")
        self.assertTrue(rows[1].response_content_non_empty)
        self.assertEqual(invoke_mock.call_count, 2)
        self.assertEqual([used.kwargs["max_output_tokens"] for used in invoke_mock.call_args_list], [10, 64])

    def test_missing_key_and_http_503_are_classified_without_messages(self):
        openrouter = self.configs()[1]
        with (
            patch.object(
                health,
                "provider_config",
                side_effect=[
                    ProviderClientError("groq_api_key_missing"),
                    openrouter,
                ],
            ),
            patch.object(
                health,
                "invoke_provider",
                side_effect=ProviderClientError(
                    "provider_http_503",
                    diagnostics={"http_status": 503, "provider_error_type": "server_error", "provider_error_code": "temporarily_unavailable"},
                ),
            ),
        ):
            rows = tuple(row for provider in health.PROVIDERS for row in health.check_provider(provider))

        self.assertEqual([row.status for row in rows], ["auth error", "server error"])
        self.assertEqual(rows[0].provider_error_code, "groq_api_key_missing")
        self.assertEqual(rows[1].http_status, 503)
        self.assertEqual(rows[1].provider_error_type, "server_error")
        self.assertEqual(rows[1].provider_error_code, "temporarily_unavailable")
        self.assertEqual(rows[1].error_code, "http_5xx")

    def test_error_categories_never_collapse_to_other_error(self):
        cases = [
            (ProviderClientError("http_401", diagnostics={"http_status": 401}), "auth error", "http_401"),
            (ProviderClientError("http_403", diagnostics={"http_status": 403}), "auth error", "http_403"),
            (ProviderClientError("http_429", diagnostics={"http_status": 429}), "rate limit", "http_429"),
            (ProviderClientError("http_5xx", outcome="unknown", diagnostics={"http_status": 503}), "server error", "http_5xx"),
            (ProviderClientError("timeout", outcome="unknown"), "timeout", "timeout"),
            (ProviderClientError("network", outcome="unknown"), "network error", "network"),
            (ProviderClientError("bad_json", outcome="unknown"), "bad JSON", "bad_json"),
            (ProviderClientError("bad_response", outcome="unknown"), "invalid response", "bad_response"),
            (ProviderClientError("http_404", diagnostics={"http_status": 404, "provider_error_code": "model_not_found"}), "model error", "model_not_found"),
            (ProviderClientError("groq_api_key_missing"), "auth error", "groq_api_key_missing"),
            (ProviderClientError("provider_model_invalid"), "model error", "provider_model_invalid"),
            (ProviderClientError("http_418", diagnostics={"http_status": 418}), "HTTP error", "http_418"),
            (ProviderClientError("provider_internal_error", outcome="unknown"), "provider error", "provider_internal_error"),
        ]
        for error, expected_status, expected_code in cases:
            with self.subTest(code=error.code, expected=expected_status):
                result = health._exception_result(
                    "groq", "model", 10, health.time.monotonic(), error
                )
                self.assertEqual(result.status, expected_status)
                self.assertEqual(result.error_code, expected_code)
                self.assertNotEqual(result.status, "other error")

    def test_error_codes_are_exposed_in_owner_panel_history_and_web_console(self):
        worker = (ROOT / "src" / "index.js").read_text(encoding="utf-8")
        owner_panel = (ROOT / "src" / "owner_panel.js").read_text(encoding="utf-8")
        screens = (ROOT / "src" / "panel_screens.js").read_text(encoding="utf-8")
        console = (ROOT / "docs" / "console" / "index.html").read_text(encoding="utf-8")

        # Read only: show the latest known failure code even after a cooldown expires.
        self.assertIn("SELECT error_code FROM ai_provider_calls WHERE provider = ? AND error_code IS NOT NULL", worker)
        self.assertIn("last_error_code: (latestError && latestError.error_code)", worker)
        self.assertIn("clean(d.last_error_code, 60)", owner_panel)
        # /ailog and the web console already render error_code from the call ledger.
        self.assertIn("SELECT provider, role, status, error_code, started_at FROM ai_provider_calls", screens)
        self.assertIn("tag(x.error_code, 24)", screens)
        self.assertIn('{label:"کد خطا",key:"error_code"}', console)
        self.assertIn('String(d.last_error_code||"—")', console)

    def test_workflow_is_manual_only_and_uses_safe_health_check(self):
        workflow = (ROOT / ".github" / "workflows" / "ai-health-check.yml").read_text(encoding="utf-8")
        trigger = workflow.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        for forbidden_trigger in ("\n  push:", "\n  schedule:", "\n  workflow_run:"):
            self.assertNotIn(forbidden_trigger, trigger)
        self.assertIn("secrets.GROQ_API_KEY", workflow)
        self.assertIn("secrets.OPENROUTER_API_KEY", workflow)
        self.assertIn("python -m agent.brain.ai_health_check", workflow)

        source = (ROOT / "agent" / "brain" / "ai_health_check.py").read_text(encoding="utf-8")
        self.assertIn("max_output_tokens=max_tokens", source)
        self.assertIn("MAX_OUTPUT_TOKENS = 10", source)
        self.assertIn("FOLLOWUP_OUTPUT_TOKENS = 64", source)
        self.assertIn('"empty response"', source)
        self.assertNotIn("call_provider_with_ledger(", source)
        self.assertNotIn("CodeAIUsageLedger", source)
        self.assertIn('print(summary, end="")', source)
        self.assertIn('handle.write(summary)', source)

    def test_log_and_job_summary_receive_same_table_without_provider_text(self):
        row = health.ProviderHealthResult(
            "groq", "openai/gpt-oss-20b", 10, "ok", 42, 200, None, None, None, True, "stop"
        )
        with tempfile.TemporaryDirectory() as folder:
            summary_path = Path(folder) / "summary.md"
            output = StringIO()
            with patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary_path)}):
                with patch.object(health, "check_provider", return_value=(row,)):
                    with redirect_stdout(output):
                        health.main()
            logged = output.getvalue()
            summary = summary_path.read_text(encoding="utf-8")
        self.assertEqual(logged, summary)
        self.assertIn("Error code", summary)
        self.assertIn("Content non-empty", summary)
        self.assertIn("finish_reason", summary)


if __name__ == "__main__":
    unittest.main()
