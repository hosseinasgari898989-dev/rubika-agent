from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from agent.brain import ai_provider_clients as clients


CALL_ID = "gemini-test-call-identifier-01"


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.raw if size < 0 else self.raw[:size]


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.request = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        if self.error:
            raise self.error
        return self.response


class FakeLedger:
    def __init__(self, fail_reserve=False, fail_finish=False):
        self.fail_reserve = fail_reserve
        self.fail_finish = fail_finish
        self.events = []

    def reserve(self, *, provider, role, run_id=None):
        self.events.append(("reserve", provider, role, run_id))
        if self.fail_reserve:
            raise clients.UsageLedgerError("usage_ledger_unreachable")
        return {"reserved": True, "call_id": CALL_ID}

    def finish(self, *, provider, call_id, outcome, error_code=None, usage=None):
        self.events.append(("finish", provider, call_id, outcome, error_code, dict(usage or {})))
        if self.fail_finish:
            raise clients.UsageLedgerError("usage_ledger_unreachable")
        return {"ok": True, "call_id": call_id, "outcome": outcome}


class GeminiProviderClientTests(unittest.TestCase):
    def setUp(self):
        self.environment = {"GEMINI_API_KEY": "g" * 32}

    def test_only_gemini_and_diagnostic_translation_roles_are_allowed(self):
        cfg = clients.provider_config("gemini", environ=self.environment)
        self.assertEqual(cfg.provider, "gemini")
        self.assertIn("generativelanguage.googleapis.com", cfg.url)
        self.assertEqual(cfg.model, "gemini-3.8-flash")
        for retired in ("groq", "openrouter", "github_models", "mistral"):
            with self.subTest(provider=retired), self.assertRaises(clients.ProviderClientError) as caught:
                clients.provider_config(retired, environ={})
            self.assertEqual(caught.exception.code, "provider_not_enabled")
        with self.assertRaises(clients.ProviderClientError) as caught:
            clients.call_provider_with_ledger(
                provider="gemini", role="propose_patch", prompt="do not patch",
                usage_client=FakeLedger(),
            )
        self.assertEqual(caught.exception.code, "provider_role_invalid")

    def test_gemini_diagnosis_posts_json_and_reads_safe_response(self):
        cfg = clients.provider_config("gemini", environ=self.environment)
        response = FakeResponse({
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": '{"summary":"confirmed test","confirmed_evidence":[],"likely_causes":[],"unknowns":[],"next_checks":[],"confidence":"low"}'}]},
            }],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 12, "totalTokenCount": 22},
        })
        opener = FakeOpener(response=response)
        with patch.object(clients.urllib.request, "build_opener", return_value=opener):
            result = clients.invoke_provider(cfg, "diagnose this safe synthetic incident", role="diagnose")
        body = json.loads(opener.request.data.decode("utf-8"))
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(result.provider, "gemini")
        self.assertIn("confirmed test", result.text)
        self.assertEqual(result.usage["totalTokenCount"], 22)

    def test_translation_requests_plain_text_and_does_not_use_other_providers(self):
        cfg = clients.provider_config("gemini", environ=self.environment)
        response = FakeResponse({
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": "ترجمهٔ فارسی معتبر"}]},
            }],
            "usageMetadata": {"totalTokenCount": 7},
        })
        opener = FakeOpener(response=response)
        with patch.object(clients.urllib.request, "build_opener", return_value=opener):
            result = clients.invoke_provider(cfg, "Translate this", role="translate_official")
        body = json.loads(opener.request.data.decode("utf-8"))
        self.assertEqual(body["generationConfig"]["responseMimeType"], "text/plain")
        self.assertEqual(result.text, "ترجمهٔ فارسی معتبر")
        self.assertNotIn("Authorization", opener.request.headers)

    def test_503_diagnostic_preserves_reason_without_raw_body(self):
        cfg = clients.provider_config("gemini", environ=self.environment)
        raw_body = json.dumps({
            "error": {
                "status": "UNAVAILABLE",
                "message": "The model is under high demand.",
                "details": [{"reason": "TEMPORARY_UNAVAILABLE"}],
            }
        }).encode()
        error = HTTPError(cfg.url, 503, "Unavailable", {}, io.BytesIO(raw_body))
        opener = FakeOpener(error=error)
        with patch.object(clients.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(clients.ProviderClientError) as caught:
                clients.invoke_provider(cfg, "diagnose one incident", role="diagnose")
        self.assertEqual(caught.exception.code, "provider_http_503")
        self.assertEqual(caught.exception.outcome, "unknown")
        self.assertEqual(caught.exception.diagnostics["provider_reason"], "UNAVAILABLE")
        self.assertIn("high demand", caught.exception.diagnostics["provider_message"])
        self.assertNotIn(raw_body.decode("utf-8"), str(caught.exception))

    def test_ledger_reserves_before_request_and_preserves_unknown_error_code(self):
        ledger = FakeLedger()
        config = clients.ProviderConfig(
            "gemini", "g" * 32, "gemini-3.8-flash",
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent",
        )
        with patch.object(clients, "provider_config", return_value=config), patch.object(
            clients, "invoke_provider",
            side_effect=clients.ProviderClientError(
                "provider_http_503", outcome="unknown",
                diagnostics={"http_status": 503, "provider_reason": "UNAVAILABLE"},
            ),
        ) as invoke:
            with self.assertRaises(clients.ProviderClientError) as caught:
                clients.call_provider_with_ledger(
                    provider="gemini", role="diagnose", prompt="safe prompt",
                    usage_client=ledger,
                )
        self.assertEqual(caught.exception.code, "provider_http_503")
        self.assertEqual(ledger.events[0][:3], ("reserve", "gemini", "diagnose"))
        self.assertEqual(ledger.events[1][0:5], ("finish", "gemini", CALL_ID, "unknown", "provider_http_503"))
        invoke.assert_called_once()

    def test_reservation_failure_prevents_provider_request(self):
        ledger = FakeLedger(fail_reserve=True)
        config = clients.ProviderConfig(
            "gemini", "g" * 32, "gemini-3.8-flash",
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent",
        )
        with patch.object(clients, "provider_config", return_value=config), patch.object(
            clients, "invoke_provider"
        ) as invoke:
            with self.assertRaises(clients.UsageLedgerError):
                clients.call_provider_with_ledger(
                    provider="gemini", role="diagnose", prompt="safe prompt",
                    usage_client=ledger,
                )
        invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
