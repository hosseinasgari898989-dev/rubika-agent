from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from agent.brain.ai_usage_ledger import ALLOWED_PROVIDERS, CodeAIUsageLedger, UsageLedgerError


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


class AIUsageLedgerTests(unittest.TestCase):
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
        self.assertNotIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", workflow)
        self.assertNotIn("AI.run", workflow)
        self.assertIn('- "Daily Rubika queue processor"', workflow)
        self.assertIn('- "Online Termux discovery and queue sync"', workflow)
        self.assertIn('contains(fromJSON(\'["push","schedule","workflow_dispatch"]\')', workflow)
        self.assertIn("id: incident_safety", workflow)
        self.assertIn("still pointed at the test channel", workflow)
        self.assertIn("bot_api_status=", workflow)
        self.assertIn("code_ai_daily_budget_exhausted", workflow)
        self.assertIn('if: steps.incident_safety.outputs.should_run == \'true\'', workflow)
        self.assertIn('if: steps.validation.outputs.tests_passed == \'true\'', workflow)
        self.assertIn('attempts="$(python', workflow)
        self.assertIn('"$attempts" -ge 4', workflow)
        provider_source = (root / "agent" / "brain" / "ai_provider_clients.py").read_text(encoding="utf-8")
        helper_source = provider_source.split("def call_provider_with_ledger(", 1)[1]
        self.assertLess(helper_source.index("ledger.reserve("), helper_source.index("invoke_provider("))
        self.assertLess(helper_source.index("invoke_provider("), helper_source.rindex("ledger.finish("))


    def test_live_provider_smoke_is_manual_only(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "multi-ai-live-smoke.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("\n  push:\n", workflow)
        self.assertIn("agent.brain.ai_usage_ledger", workflow)
        self.assertIn("GROQ_API_KEY", workflow)
        self.assertIn("OPENROUTER_API_KEY", workflow)

if __name__ == "__main__":
    unittest.main()
