from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from agent.brain import ai_provider_clients as clients
from agent.brain.gemini_usage_client import UsageLedgerError


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        return self.raw[:size]


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        if self.error:
            raise self.error
        return self.response


class FakeLedger:
    def __init__(self, *, fail_reserve=False, fail_finish=False):
        self.fail_reserve = fail_reserve
        self.fail_finish = fail_finish
        self.events = []

    def reserve(self, *, provider, role, run_id=None):
        self.events.append(("reserve", provider, role, run_id))
        if self.fail_reserve:
            raise UsageLedgerError("code_ai_ledger_unavailable")
        return {"reserved": True, "call_id": "test-provider-call-0001"}

    def finish(self, *, provider, call_id, outcome, error_code=None, usage=None):
        self.events.append(("finish", provider, outcome, error_code, dict(usage or {})))
        if self.fail_finish:
            raise UsageLedgerError("usage_ledger_unreachable")
        return {"ok": True, "call_id": call_id, "outcome": outcome}


class ProviderClientTests(unittest.TestCase):
    def test_provider_configuration_is_allowlisted_and_secrets_are_required(self):
        cfg = clients.provider_config(
            "groq",
            environ={"GROQ_API_KEY": "g" * 32, "GROQ_MODEL": "llama-3.3-70b-versatile"},
        )
        self.assertEqual(cfg.url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(cfg.model, "llama-3.3-70b-versatile")
        with self.assertRaises(clients.ProviderClientError) as caught:
            clients.provider_config("unknown-provider", environ={})
        self.assertEqual(caught.exception.code, "provider_not_enabled")
        with self.assertRaises(clients.ProviderClientError) as caught:
            clients.provider_config("openrouter", environ={})
        self.assertEqual(caught.exception.code, "openrouter_api_key_missing")

    def test_current_provider_defaults_are_explicit_and_configurable(self):
        groq = clients.provider_config("groq", environ={"GROQ_API_KEY": "g" * 32})
        self.assertEqual(groq.model, "openai/gpt-oss-20b")
        self.assertEqual(groq.url, "https://api.groq.com/openai/v1/chat/completions")

        openrouter = clients.provider_config("openrouter", environ={"OPENROUTER_API_KEY": "a" * 32})
        self.assertEqual(openrouter.model, "openrouter/free")
        self.assertEqual(openrouter.url, "https://openrouter.ai/api/v1/chat/completions")

        gemini = clients.provider_config("gemini", environ={"GEMINI_API_KEY": "m" * 32})
        self.assertEqual(gemini.model, "gemini-3.8-flash")
        self.assertTrue(gemini.url.startswith("https://generativelanguage.googleapis.com/"))

    def test_gemini_response_is_parsed_and_api_key_is_only_a_header(self):
        config = clients.ProviderConfig(
            "gemini",
            "g" * 32,
            "gemini-3.8-flash",
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent",
        )
        opener = FakeOpener(FakeResponse({
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": '{"ok": true}'}]},
            }],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            },
        }))
        with patch.object(clients.urllib.request, "build_opener", return_value=opener):
            result = clients.invoke_provider(config, "safe prompt")
        self.assertEqual(result.text, '{"ok": true}')
        self.assertEqual(result.usage["totalTokenCount"], 15)
        self.assertNotIn(config.api_key, opener.request.full_url)
        self.assertEqual(opener.request.get_header("X-goog-api-key"), config.api_key)
        request_body = json.loads(opener.request.data.decode("utf-8"))
        self.assertEqual(request_body["generationConfig"]["responseMimeType"], "application/json")

    def test_groq_openai_compatible_response_is_normalized(self):
        config = clients.ProviderConfig(
            "groq", "r" * 32, "llama-3.3-70b-versatile",
            "https://api.groq.com/openai/v1/chat/completions",
        )
        opener = FakeOpener(FakeResponse({
            "choices": [{"message": {"content": "diagnosis result"}}],
            "usage": {"prompt_tokens": 21, "completion_tokens": 8, "total_tokens": 29},
        }))
        with patch.object(clients.urllib.request, "build_opener", return_value=opener):
            result = clients.invoke_provider(config, "safe prompt")
        self.assertEqual(result.text, "diagnosis result")
        self.assertEqual(result.usage["promptTokenCount"], 21)
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer " + config.api_key)
        self.assertNotIn(config.api_key, opener.request.full_url)

    def test_openrouter_chat_completion_response_is_normalized(self):
        config = clients.ProviderConfig(
            "openrouter", "o" * 32, "openrouter/free",
            "https://openrouter.ai/api/v1/chat/completions",
        )
        opener = FakeOpener(FakeResponse({
            "choices": [{"message": {"content": '{"decision":"approve"}'}}],
            "usage": {"prompt_tokens": 35, "completion_tokens": 12, "total_tokens": 47},
        }))
        with patch.object(clients.urllib.request, "build_opener", return_value=opener):
            result = clients.invoke_provider(config, "review request")
        self.assertEqual(result.text, '{"decision":"approve"}')
        self.assertEqual(result.usage["totalTokenCount"], 47)
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer " + config.api_key)
        self.assertNotIn(config.api_key, opener.request.full_url)
        self.assertEqual(json.loads(opener.request.data.decode("utf-8"))["model"], "openrouter/free")

    def test_provider_429_is_a_safe_rate_limited_error_without_raw_body(self):
        config = clients.ProviderConfig(
            "groq", "r" * 32, "model",
            "https://api.groq.com/openai/v1/chat/completions",
        )
        error = HTTPError(config.url, 429, "rate limited", {}, io.BytesIO(b"secret response body"))
        opener = FakeOpener(error=error)
        with patch.object(clients.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(clients.ProviderClientError) as caught:
                clients.invoke_provider(config, "safe prompt")
        self.assertEqual(caught.exception.code, "provider_http_429")
        self.assertEqual(caught.exception.outcome, "failed")
        self.assertNotIn("secret response body", str(caught.exception))

    def test_ledger_reservation_happens_before_provider_request_and_result_is_finished(self):
        ledger = FakeLedger()
        result = clients.ProviderResult("groq", "model", "answer", {"totalTokenCount": 4})
        sequence = []

        def invoke(config, prompt, *, max_output_tokens):
            sequence.append("request")
            return result

        with patch.object(clients, "provider_config", return_value=clients.ProviderConfig(
            "groq", "r" * 32, "model", "https://api.groq.com/openai/v1/chat/completions"
        )), patch.object(clients, "invoke_provider", side_effect=invoke):
            actual = clients.call_provider_with_ledger(
                provider="groq",
                role="diagnose",
                prompt="safe prompt",
                usage_client=ledger,
                on_reserved=lambda: sequence.append("reserved"),
            )
        self.assertIs(actual, result)
        self.assertEqual(sequence, ["reserved", "request"])
        self.assertEqual(ledger.events[0][0], "reserve")
        self.assertEqual(ledger.events[1][0:3], ("finish", "groq", "succeeded"))

    def test_no_provider_request_occurs_when_reservation_fails(self):
        ledger = FakeLedger(fail_reserve=True)
        with patch.object(clients, "provider_config", return_value=clients.ProviderConfig(
            "gemini", "g" * 32, "model", "https://example.invalid"
        )), patch.object(clients, "invoke_provider") as invoke:
            with self.assertRaises(UsageLedgerError):
                clients.call_provider_with_ledger(
                    provider="gemini", role="propose_patch", prompt="safe prompt", usage_client=ledger
                )
        invoke.assert_not_called()

    def test_result_is_blocked_if_ledger_finish_fails(self):
        ledger = FakeLedger(fail_finish=True)
        result = clients.ProviderResult("openrouter", "model", "answer", {})
        with patch.object(clients, "provider_config", return_value=clients.ProviderConfig(
            "openrouter", "a" * 32, "model", "https://openrouter.ai/api/v1/chat/completions"
        )), patch.object(clients, "invoke_provider", return_value=result):
            with self.assertRaises(clients.ProviderClientError) as caught:
                clients.call_provider_with_ledger(
                    provider="openrouter", role="review_patch", prompt="review", usage_client=ledger
                )
        self.assertEqual(caught.exception.code, "usage_ledger_finish_failed")


if __name__ == "__main__":
    unittest.main()
