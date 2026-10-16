from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from agent.brain import gemini_code_agent as gemini
from agent.brain.gemini_usage_client import ALLOWED_PROVIDERS, CodeAIUsageLedger, UsageLedgerError


CALL_ID = "test-call-identifier-123456"


class FakeResponse:
    def __init__(self, payload, status=200):
        self.body = json.dumps(payload).encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.body


class FakeLedger:
    def __init__(self, *, reserve_error=None, finish_error=None):
        self.events = []
        self.reserve_error = reserve_error
        self.finish_error = finish_error

    def reserve(self, **kwargs):
        self.events.append(("reserve", kwargs))
        if self.reserve_error:
            raise self.reserve_error
        return {"ok": True, "reserved": True, "call_id": CALL_ID}

    def finish(self, **kwargs):
        self.events.append(("finish", kwargs))
        if self.finish_error:
            raise self.finish_error
        return {"ok": True, "call_id": kwargs["call_id"], "outcome": kwargs["outcome"]}


class GeminiUsageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.proposal = {
            "summary": "Safe synthetic proposal",
            "root_cause": "Fixture failure",
            "patch": "",
            "confidence": "low",
            "manual_review": True,
        }
        self.usage = {
            "promptTokenCount": 120,
            "candidatesTokenCount": 30,
            "totalTokenCount": 150,
        }

    def test_required_ledger_reserves_before_one_gemini_call_and_finishes_success(self):
        events = []
        ledger = FakeLedger()
        ledger.events = events

        def call_gemini(**kwargs):
            events.append(("model", kwargs))
            return self.proposal, self.usage

        def after_reservation():
            events.append(("request_marked", {}))

        with patch.object(gemini, "call_gemini", side_effect=call_gemini) as model:
            proposal, usage = gemini.call_gemini_with_ledger(
                api_key="fake-gemini-key-for-offline-test-123",
                model="gemini-test-model",
                prompt="safe synthetic prompt",
                required=True,
                run_id="123456789_1",
                usage_client=ledger,
                on_reserved=after_reservation,
            )

        self.assertEqual(proposal, self.proposal)
        self.assertEqual(usage, self.usage)
        self.assertEqual([event[0] for event in events], ["reserve", "request_marked", "model", "finish"])
        self.assertEqual(events[0][1]["provider"], "gemini")
        self.assertEqual(events[0][1]["role"], "propose_patch")
        self.assertEqual(events[-1][1]["outcome"], "succeeded")
        self.assertEqual(events[-1][1]["usage"], self.usage)
        model.assert_called_once()

    def test_reservation_failure_blocks_gemini_request(self):
        ledger = FakeLedger(reserve_error=UsageLedgerError("code_ai_daily_budget_exhausted"))
        on_reserved = []
        with patch.object(gemini, "call_gemini") as model:
            with self.assertRaisesRegex(UsageLedgerError, "code_ai_daily_budget_exhausted"):
                gemini.call_gemini_with_ledger(
                    api_key="fake-gemini-key-for-offline-test-123",
                    model="gemini-test-model",
                    prompt="synthetic prompt",
                    required=True,
                    usage_client=ledger,
                    on_reserved=lambda: on_reserved.append(True),
                )
        model.assert_not_called()
        self.assertEqual(on_reserved, [])
        self.assertEqual(len(ledger.events), 1)
        self.assertEqual(ledger.events[0][0], "reserve")

    def test_rate_limit_finishes_reservation_and_requests_cooldown(self):
        ledger = FakeLedger()
        on_reserved = []
        with patch.object(gemini, "call_gemini", side_effect=gemini.GeminiAssistantError("gemini_http_429")):
            with self.assertRaisesRegex(gemini.GeminiAssistantError, "gemini_http_429"):
                gemini.call_gemini_with_ledger(
                    api_key="fake-gemini-key-for-offline-test-123",
                    model="gemini-test-model",
                    prompt="synthetic prompt",
                    required=True,
                    usage_client=ledger,
                    on_reserved=lambda: on_reserved.append(True),
                )
        finish = ledger.events[-1][1]
        self.assertEqual([event[0] for event in ledger.events], ["reserve", "finish"])
        self.assertEqual(finish["outcome"], "failed")
        self.assertEqual(finish["error_code"], "rate_limited")
        self.assertEqual(on_reserved, [True])

    def test_ambiguous_network_failure_is_counted_as_unknown(self):
        ledger = FakeLedger()
        with patch.object(gemini, "call_gemini", side_effect=gemini.GeminiAssistantError("gemini_network_error")):
            with self.assertRaisesRegex(gemini.GeminiAssistantError, "gemini_network_error"):
                gemini.call_gemini_with_ledger(
                    api_key="fake-gemini-key-for-offline-test-123",
                    model="gemini-test-model",
                    prompt="synthetic prompt",
                    required=True,
                    usage_client=ledger,
                )
        finish = ledger.events[-1][1]
        self.assertEqual(finish["outcome"], "unknown")
        self.assertEqual(finish["error_code"], "provider_result_unknown")

    def test_ledger_finish_failure_blocks_returning_proposal(self):
        ledger = FakeLedger(finish_error=UsageLedgerError("usage_ledger_unreachable"))
        with patch.object(gemini, "call_gemini", return_value=(self.proposal, self.usage)):
            with self.assertRaisesRegex(gemini.GeminiAssistantError, "usage_ledger_finish_failed"):
                gemini.call_gemini_with_ledger(
                    api_key="fake-gemini-key-for-offline-test-123",
                    model="gemini-test-model",
                    prompt="synthetic prompt",
                    required=True,
                    usage_client=ledger,
                )
        self.assertEqual(len(ledger.events), 2)

    def test_legacy_offline_mode_does_not_attempt_ledger_access(self):
        with patch.object(gemini, "call_gemini", return_value=(self.proposal, self.usage)) as model, \
             patch.object(CodeAIUsageLedger, "from_env", side_effect=AssertionError("ledger should not be loaded")):
            result = gemini.call_gemini_with_ledger(
                api_key="fake-gemini-key-for-offline-test-123",
                model="gemini-test-model",
                prompt="synthetic prompt",
                required=False,
            )
        self.assertEqual(result, (self.proposal, self.usage))
        model.assert_called_once()

    def test_ledger_client_reservation_uses_https_bearer_and_safe_endpoint(self):
        client = CodeAIUsageLedger(
            "https://example.invalid",
            "fake-ledger-token-for-offline-test-1234567890",
        )
        response = FakeResponse({"ok": True, "reserved": True, "call_id": CALL_ID}, status=201)
        with patch.object(client._opener, "open", return_value=response) as open_request:
            result = client.reserve(provider="groq", role="propose_patch", call_id=CALL_ID)
        request = open_request.call_args.args[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(request.full_url, "https://example.invalid/api/brain/ai-provider/reserve")
        self.assertNotIn(client.token, request.full_url)
        self.assertEqual(headers["authorization"], "Bearer " + client.token)
        self.assertEqual(result["call_id"], CALL_ID)

    def test_ledger_client_allowlist_matches_enabled_providers(self):
        expected = {"groq", "openrouter"}
        self.assertEqual(ALLOWED_PROVIDERS, expected)
        client = CodeAIUsageLedger(
            "https://example.invalid",
            "fake-ledger-token-for-offline-test-1234567890",
        )
        for provider in sorted(expected):
            with self.subTest(provider=provider):
                response = FakeResponse(
                    {"ok": True, "reserved": True, "call_id": CALL_ID},
                    status=201,
                )
                with patch.object(client._opener, "open", return_value=response):
                    result = client.reserve(
                        provider=provider,
                        role="diagnose",
                        call_id=CALL_ID,
                    )
                self.assertEqual(result["call_id"], CALL_ID)

    def test_ledger_client_never_follows_a_429_budget_response(self):
        client = CodeAIUsageLedger(
            "https://example.invalid",
            "fake-ledger-token-for-offline-test-1234567890",
        )
        error = HTTPError(
            "https://example.invalid/api/brain/ai-provider/reserve",
            429, "Too Many Requests", {},
            io.BytesIO(json.dumps({"ok": False, "error": "code_ai_daily_budget_exhausted"}).encode()),
        )
        with patch.object(client._opener, "open", side_effect=error):
            with self.assertRaisesRegex(UsageLedgerError, "code_ai_daily_budget_exhausted"):
                client.reserve(provider="groq", role="propose_patch", call_id=CALL_ID)

    def test_ledger_client_rejects_http_url_and_short_token(self):
        with self.assertRaisesRegex(UsageLedgerError, "usage_ledger_url_invalid"):
            CodeAIUsageLedger("http://example.invalid", "long-enough-fake-token-123456789")
        with self.assertRaisesRegex(UsageLedgerError, "usage_ledger_token_missing_or_short"):
            CodeAIUsageLedger("https://example.invalid", "short")

    def test_active_repair_workflow_uses_groq_and_openrouter_only(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "groq-openrouter-code-repair.yml").read_text(encoding="utf-8")
        self.assertIn("python -m agent.brain.multi_ai_code_repair", workflow)
        self.assertIn("AGENT_API_TOKEN: ${{ secrets.AGENT_API_TOKEN }}", workflow)
        self.assertIn("GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}", workflow)
        self.assertIn("OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}", workflow)
        self.assertIn("CODE_AI_DIAGNOSER_PROVIDERS: groq,openrouter", workflow)
        self.assertIn("CODE_AI_PROPOSER_PROVIDER: groq", workflow)
        self.assertIn("CODE_AI_REVIEWER_PROVIDERS: groq,openrouter", workflow)
        self.assertIn("status?provider=$provider", workflow)
        self.assertNotIn("GITHUB_MODELS_MODEL", workflow)
        self.assertNotIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", workflow)
        self.assertNotIn("GEMINI_API_KEY", workflow)
        self.assertNotIn("AI.run", workflow)
        provider_source = (root / "agent" / "brain" / "ai_provider_clients.py").read_text(encoding="utf-8")
        helper_source = provider_source.split("def call_provider_with_ledger(", 1)[1]
        self.assertLess(helper_source.index("ledger.reserve("), helper_source.index("invoke_provider("))
        self.assertLess(helper_source.index("invoke_provider("), helper_source.rindex("ledger.finish("))


if __name__ == "__main__":
    unittest.main()
