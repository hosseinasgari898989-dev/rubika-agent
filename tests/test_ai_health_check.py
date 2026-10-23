from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import call, patch

from agent.brain import ai_health_check as health
from agent.brain.ai_provider_clients import ProviderClientError, ProviderConfig, ProviderResult


ROOT = Path(__file__).resolve().parents[1]


class AIHealthCheckTests(unittest.TestCase):
    def test_rate_limited_provider_does_not_suppress_the_other_check(self):
        configs = [
            ProviderConfig("groq", "g" * 32, "model-groq", "https://api.groq.com/openai/v1/chat/completions"),
            ProviderConfig("openrouter", "o" * 32, "model-openrouter", "https://openrouter.ai/api/v1/chat/completions"),
        ]
        private_response = "PRIVATE FULL PROVIDER RESPONSE MUST NOT APPEAR"
        response = ProviderResult("openrouter", "model-openrouter", private_response, {})
        with (
            patch.object(health, "provider_config", side_effect=configs) as config_mock,
            patch.object(
                health,
                "invoke_provider",
                side_effect=[ProviderClientError("provider_http_429"), response],
            ) as invoke_mock,
        ):
            results = tuple(health.check_provider(provider) for provider in health.PROVIDERS)

        self.assertEqual([result.status for result in results], ["rate limit", "ok"])
        self.assertEqual(config_mock.call_args_list, [call("groq"), call("openrouter")])
        self.assertEqual(invoke_mock.call_count, 2)
        for used_call in invoke_mock.call_args_list:
            self.assertEqual(used_call.kwargs["max_output_tokens"], 10)
            self.assertEqual(used_call.kwargs["timeout_seconds"], 20)
        summary = health.format_summary(results)
        self.assertIn("rate limit", summary)
        self.assertIn("ok", summary)
        self.assertNotIn(private_response, summary)
        self.assertNotIn("g" * 32, summary)
        self.assertNotIn("o" * 32, summary)

    def test_missing_key_is_auth_error_and_unexpected_provider_error_is_other_error(self):
        openrouter_config = ProviderConfig(
            "openrouter", "o" * 32, "model-openrouter",
            "https://openrouter.ai/api/v1/chat/completions",
        )
        with (
            patch.object(
                health,
                "provider_config",
                side_effect=[ProviderClientError("groq_api_key_missing"), openrouter_config],
            ),
            patch.object(
                health,
                "invoke_provider",
                side_effect=ProviderClientError("provider_http_503"),
            ) as invoke_mock,
        ):
            results = tuple(health.check_provider(provider) for provider in health.PROVIDERS)

        self.assertEqual([result.status for result in results], ["auth error", "other error"])
        self.assertEqual(invoke_mock.call_count, 1)

    def test_workflow_is_manual_only_and_uses_the_safe_check_module(self):
        workflow = (ROOT / ".github" / "workflows" / "ai-health-check.yml").read_text(encoding="utf-8")
        trigger = workflow.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        for forbidden_trigger in ("\n  push:", "\n  schedule:", "\n  workflow_run:"):
            self.assertNotIn(forbidden_trigger, trigger)
        self.assertIn("secrets.GROQ_API_KEY", workflow)
        self.assertIn("secrets.OPENROUTER_API_KEY", workflow)
        self.assertIn("python -m agent.brain.ai_health_check", workflow)

        source = (ROOT / "agent" / "brain" / "ai_health_check.py").read_text(encoding="utf-8")
        self.assertIn("max_output_tokens=MAX_OUTPUT_TOKENS", source)
        self.assertIn("MAX_OUTPUT_TOKENS = 10", source)
        self.assertNotIn("call_provider_with_ledger(", source)
        self.assertNotIn("CodeAIUsageLedger", source)


if __name__ == "__main__":
    unittest.main()
