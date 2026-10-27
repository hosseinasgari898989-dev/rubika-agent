from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from agent.brain import ai_provider_clients as clients
from agent.brain.ai_usage_ledger import UsageLedgerError


class FakeResponse:
    def __init__(self, payload, status=200, raw=None):
        self.status = status
        self.raw = raw if raw is not None else json.dumps(payload).encode("utf-8")

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

    def reserve(self, *, provider, role, run_id=None, estimated_tokens=None):
        self.events.append(("reserve", provider, role, run_id))
        self.estimated_tokens = estimated_tokens
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
        with self.assertRaises(clients.ProviderClientError) as caught:
            clients.provider_config(
                "groq",
                environ={"GROQ_API_KEY": "g" * 32, "GROQ_MODEL": "not a valid model!"},
            )
        self.assertEqual(caught.exception.code, "groq_model_invalid")

    def test_only_current_provider_clients_are_allowlisted(self):
        self.assertEqual(set(clients.PROVIDERS), {"groq", "openrouter"})

    def test_current_provider_defaults_are_explicit_and_configurable(self):
        groq = clients.provider_config("groq", environ={"GROQ_API_KEY": "g" * 32})
        self.assertEqual(groq.model, "openai/gpt-oss-20b")
        self.assertEqual(groq.url, "https://api.groq.com/openai/v1/chat/completions")

        openrouter = clients.provider_config("openrouter", environ={"OPENROUTER_API_KEY": "a" * 32})
        self.assertEqual(openrouter.model, "openrouter/free")
        self.assertEqual(openrouter.url, "https://openrouter.ai/api/v1/chat/completions")

    def test_groq_openai_compatible_response_is_normalized(self):
        config = clients.ProviderConfig(
            "groq", "r" * 32, "llama-3.3-70b-versatile",
            "https://api.groq.com/openai/v1/chat/completions",
        )
        opener = FakeOpener(FakeResponse({
            "choices": [{"message": {"content": "diagnosis result"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 21, "completion_tokens": 8, "total_tokens": 29},
        }))
        with patch.object(clients.urllib.request, "build_opener", return_value=opener):
            result = clients.invoke_provider(config, "safe prompt")
        self.assertEqual(result.text, "diagnosis result")
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.finish_reason, "stop")
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
        self.assertEqual(caught.exception.code, "http_429")
        self.assertEqual(caught.exception.outcome, "failed")
        self.assertNotIn("secret response body", str(caught.exception))

    def test_provider_503_has_sanitized_generic_diagnostics(self):
        config = clients.ProviderConfig(
            "groq", "g" * 32, "model",
            "https://api.groq.com/openai/v1/chat/completions",
        )
        error = HTTPError(
            config.url, 503, "unavailable",
            {"Retry-After": "13"},
            io.BytesIO(b'{"error":{"type":"server_error","code":"temporary_failure","message":"must not be logged"}}'),
        )
        opener = FakeOpener(error=error)
        with patch.object(clients.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(clients.ProviderClientError) as caught:
                clients.invoke_provider(config, "safe prompt")
        self.assertEqual(caught.exception.code, "http_5xx")
        self.assertEqual(caught.exception.outcome, "unknown")
        self.assertEqual(caught.exception.diagnostics["http_status"], 503)
        self.assertEqual(caught.exception.diagnostics["retry_after_seconds"], 13)
        self.assertEqual(caught.exception.diagnostics["provider_error_type"], "server_error")
        self.assertEqual(caught.exception.diagnostics["provider_error_code"], "temporary_failure")
        self.assertNotIn("must not be logged", json.dumps(caught.exception.diagnostics))
    def test_empty_http_200_response_preserves_status_and_finish_reason_without_content(self):
        config = clients.ProviderConfig(
            "groq", "g" * 32, "model",
            "https://api.groq.com/openai/v1/chat/completions",
        )
        opener = FakeOpener(FakeResponse({
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        }))
        with patch.object(clients.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(clients.ProviderClientError) as caught:
                clients.invoke_provider(config, "safe prompt", max_output_tokens=10)
        self.assertEqual(caught.exception.code, "provider_text_missing")
        self.assertEqual(caught.exception.diagnostics["http_status"], 200)
        self.assertEqual(caught.exception.diagnostics["finish_reason"], "length")
        self.assertNotIn("response text", json.dumps(caught.exception.diagnostics))

    def test_http_statuses_and_model_not_found_have_specific_safe_codes(self):
        url = "https://api.groq.com/openai/v1/chat/completions"
        config = clients.ProviderConfig("groq", "g" * 32, "model", url)
        cases = [
            (401, b'{"error":{"code":"invalid_api_key"}}', "http_401", "failed"),
            (403, b'{"error":{"code":"permission_denied"}}', "http_403", "failed"),
            (429, b'{"error":{"code":"rate_limited"}}', "http_429", "failed"),
            (503, b'{"error":{"type":"server_error"}}', "http_5xx", "unknown"),
            (418, b'{"error":{"code":"unusual_status"}}', "http_418", "failed"),
            (404, b'{"error":{"code":"model_not_found"}}', "model_not_found", "failed"),
        ]
        for status, raw, expected_code, expected_outcome in cases:
            with self.subTest(status=status, expected_code=expected_code):
                error = HTTPError(url, status, "private response detail", {}, io.BytesIO(raw))
                with patch.object(clients.urllib.request, "build_opener", return_value=FakeOpener(error=error)):
                    with self.assertRaises(clients.ProviderClientError) as caught:
                        clients.invoke_provider(config, "safe prompt")
                self.assertEqual(caught.exception.code, expected_code)
                self.assertEqual(caught.exception.outcome, expected_outcome)
                self.assertNotIn("private response detail", str(caught.exception))
                self.assertNotIn("invalid_api_key", str(caught.exception))

    def test_timeout_network_bad_json_and_bad_shape_are_separate(self):
        config = clients.ProviderConfig(
            "groq", "g" * 32, "model",
            "https://api.groq.com/openai/v1/chat/completions",
        )
        cases = [
            (FakeOpener(error=TimeoutError("private timeout detail")), "timeout", "unknown"),
            (FakeOpener(error=URLError("private network detail")), "network", "unknown"),
            (FakeOpener(error=URLError(TimeoutError("private wrapped timeout"))), "timeout", "unknown"),
            (FakeOpener(response=FakeResponse(None, status=429)), "http_429", "failed"),
            (FakeOpener(response=FakeResponse(None, status=502)), "http_5xx", "unknown"),
            (FakeOpener(response=FakeResponse(None, raw=b"{not-json")), "bad_json", "unknown"),
            (FakeOpener(response=FakeResponse({"unexpected": True})), "bad_response", "unknown"),
            (FakeOpener(response=FakeResponse(None, raw=b"x" * (clients.MAX_RESPONSE_BYTES + 1))), "response_too_large", "unknown"),
        ]
        for opener, expected_code, expected_outcome in cases:
            with self.subTest(expected_code=expected_code):
                with patch.object(clients.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaises(clients.ProviderClientError) as caught:
                        clients.invoke_provider(config, "safe prompt")
                self.assertEqual(caught.exception.code, expected_code)
                self.assertEqual(caught.exception.outcome, expected_outcome)
                self.assertNotIn("private", str(caught.exception))

    def test_http_429_keeps_existing_rate_limited_ledger_cooldown_code(self):
        ledger = FakeLedger()
        config = clients.ProviderConfig(
            "groq", "g" * 32, "model",
            "https://api.groq.com/openai/v1/chat/completions",
        )
        with (
            patch.object(clients, "provider_config", return_value=config),
            patch.object(clients, "invoke_provider", side_effect=clients.ProviderClientError("http_429", outcome="failed")),
        ):
            with self.assertRaises(clients.ProviderClientError):
                clients.call_provider_with_ledger(
                    provider="groq", role="diagnose", prompt="safe prompt", usage_client=ledger
                )
        self.assertEqual(ledger.events[0][0], "reserve")
        self.assertEqual(ledger.events[1][0:4], ("finish", "groq", "failed", "rate_limited"))

    def test_ledger_reservation_happens_before_provider_request_and_result_is_finished(self):
        ledger = FakeLedger()
        result = clients.ProviderResult("groq", "model", "answer", {"totalTokenCount": 4})
        sequence = []
        timeouts = []

        def invoke(config, prompt, *, max_output_tokens, timeout_seconds=45, role=None, call_id=None):
            sequence.append("request")
            timeouts.append(timeout_seconds)
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
        self.assertEqual(timeouts, [45])
        self.assertEqual(ledger.events[0][0], "reserve")
        self.assertEqual(ledger.events[1][0:3], ("finish", "groq", "succeeded"))

    def test_no_provider_request_occurs_when_reservation_fails(self):
        ledger = FakeLedger(fail_reserve=True)
        with patch.object(clients, "provider_config", return_value=clients.ProviderConfig(
            "groq", "g" * 32, "model", "https://api.groq.com/openai/v1/chat/completions"
        )), patch.object(clients, "invoke_provider") as invoke:
            with self.assertRaises(UsageLedgerError):
                clients.call_provider_with_ledger(
                    provider="groq", role="propose_patch", prompt="safe prompt", usage_client=ledger
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
