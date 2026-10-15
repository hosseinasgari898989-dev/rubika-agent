from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from termux import interactive_agent as agent


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeBotAPI:
    def __init__(self):
        self.sent = []

    def get_me(self):
        return {"status": "OK"}

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {"status": "OK"}


class RubikaBotAPITests(unittest.TestCase):
    def test_call_uses_fixed_rubika_host_and_safe_allowlisted_method(self):
        response = FakeResponse({"status": "OK", "data": {}})
        with patch.object(agent.requests, "post", return_value=response) as post:
            api = agent.RubikaBotAPI("test-private-token-0123456789", timeout_seconds=4)
            result = api.call("getMe", {})
        self.assertEqual(result["status"], "OK")
        request_url = post.call_args.args[0]
        self.assertEqual(request_url, "https://botapi.rubika.ir/v3/test-private-token-0123456789/getMe")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        with self.assertRaisesRegex(agent.SafeAgentError, "rubika_method_not_allowed"):
            api.call("deleteMessage", {})

    def test_transport_exception_never_exposes_token_bearing_url(self):
        token = "top-secret-rubika-token-0123456789"
        with patch.object(agent.requests, "post", side_effect=requests.Timeout()):
            api = agent.RubikaBotAPI(token)
            with self.assertRaises(agent.SafeAgentError) as caught:
                api.get_me()
        self.assertEqual(caught.exception.code, "rubika_network_timeout")
        self.assertNotIn(token, str(caught.exception))

    def test_bad_api_status_is_reduced_to_safe_code(self):
        with patch.object(agent.requests, "post", return_value=FakeResponse({"status": "INVALID_INPUT"})):
            api = agent.RubikaBotAPI("test-private-token-0123456789")
            with self.assertRaisesRegex(agent.SafeAgentError, "rubika_api_invalid_input"):
                api.get_me()


class PrivateMessageGateTests(unittest.TestCase):
    def _update(self, *, chat_id="owner-id-123", sender_id="owner-id-123", update_type="NewMessage", extra=None):
        update = {
            "type": update_type,
            "chat_id": chat_id,
            "new_message": {
                "message_id": "message-123",
                "sender_id": sender_id,
                "text": "/help",
            },
        }
        if extra:
            update.update(extra)
        return update

    def test_accepts_only_owner_private_text(self):
        update = self._update()
        self.assertEqual(agent._owner_private_text(update, "owner-id-123"), "/help")

    def test_rejects_other_sender_and_other_chat(self):
        self.assertIsNone(agent._owner_private_text(
            self._update(sender_id="someone-else"), "owner-id-123"
        ))
        self.assertIsNone(agent._owner_private_text(
            self._update(chat_id="group-id-123"), "owner-id-123"
        ))

    def test_rejects_group_channel_and_edited_messages(self):
        self.assertIsNone(agent._owner_private_text(
            self._update(extra={"chat_type": "group"}), "owner-id-123"
        ))
        self.assertIsNone(agent._owner_private_text(
            self._update(update_type="UpdatedMessage"), "owner-id-123"
        ))

    def test_requires_message_identity_to_deduplicate(self):
        self.assertIsNone(agent._message_identity({"type": "NewMessage", "chat_id": "owner-id"}))


class RepositoryContextTests(unittest.TestCase):
    def test_retrieval_reads_source_but_excludes_config_artifacts_and_env_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "agent").mkdir()
            (root / "config").mkdir()
            (root / "artifacts").mkdir()
            (root / "agent" / "demo.py").write_text(
                "def cloud_queue_worker():\n    return 'safe project context'\n",
                encoding="utf-8",
            )
            (root / "config" / "private.py").write_text(
                "PRIVATE_KEY = 'SHOULD_NEVER_APPEAR_IN_CONTEXT'\n",
                encoding="utf-8",
            )
            (root / "artifacts" / "report.md").write_text(
                "queued report should not be shared",
                encoding="utf-8",
            )
            (root / ".env").write_text("GROQ_API_KEY=SHOULD_NEVER_APPEAR", encoding="utf-8")
            context = agent.RepositoryContext(root).retrieve("cloud queue worker")
            self.assertIn("agent/demo.py", context)
            self.assertIn("cloud_queue_worker", context)
            self.assertNotIn("SHOULD_NEVER_APPEAR", context)
            self.assertNotIn("private.py", context)
            self.assertNotIn("artifacts/report.md", context)

    def test_secret_like_text_is_redacted_before_context_is_returned(self):
        text = "GROQ_API_KEY = 'gsk_" + ("A" * 32) + "'\nAuthorization: Bearer secret-value"
        clean = agent.redact_sensitive_text(text)
        self.assertNotIn("gsk_" + ("A" * 32), clean)
        self.assertNotIn("Bearer secret-value", clean)
        self.assertIn("[REDACTED]", clean)


class ProviderClientTests(unittest.TestCase):
    def test_groq_request_is_single_bounded_request_and_parses_text(self):
        response = FakeResponse({
            "choices": [{"message": {"content": "سلام؛ این پاسخ آزمایشی است."}}]
        })
        key = "test-groq-api-key-not-real-0123456789"
        client = agent.ProviderClient("groq", environ={"GROQ_API_KEY": key})
        with patch.object(agent.requests, "post", return_value=response) as post:
            answer = client.complete([
                {"role": "system", "content": "system"},
                {"role": "user", "content": "question"},
            ])
        self.assertEqual(answer, "سلام؛ این پاسخ آزمایشی است.")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer " + key)
        self.assertNotIn(key, post.call_args.args[0])
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], 1100)

    def test_rate_limit_is_not_retried_or_fallen_back(self):
        client = agent.ProviderClient("openrouter", environ={
            "OPENROUTER_API_KEY": "test-openrouter-key-not-real-0123456789"
        })
        with patch.object(agent.requests, "post", return_value=FakeResponse({}, status_code=429)) as post:
            with self.assertRaisesRegex(agent.SafeAgentError, "provider_http_429_no_retry"):
                client.complete([{"role": "user", "content": "question"}])
        self.assertEqual(post.call_count, 1)

    def test_missing_key_is_detected_without_network_request(self):
        with self.assertRaisesRegex(agent.SafeAgentError, "groq_api_key_missing"):
            agent.ProviderClient("groq", environ={})

    def test_unknown_provider_is_rejected(self):
        with self.assertRaisesRegex(agent.SafeAgentError, "interactive_provider_not_supported"):
            agent.ProviderClient("openai", environ={"OPENAI_API_KEY": "not-used"})


class LocalStateTests(unittest.TestCase):
    def test_state_keeps_cursor_and_quota_but_not_conversation_or_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = agent.LocalState(path)
            state.data["offset_id"] = "cursor-123"
            state.mark_processed("a" * 64)
            state.reserve_ai_attempt()
            state.save()
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            self.assertEqual(parsed["offset_id"], "cursor-123")
            self.assertEqual(parsed["processed_ids"], ["a" * 64])
            self.assertEqual(parsed["ai_requests_today"], 1)
            self.assertNotIn("conversation", raw)
            self.assertNotIn("API_KEY", raw)
            self.assertNotIn("token-secret", raw)
            loaded = agent.LocalState(path)
            self.assertEqual(loaded.data["offset_id"], "cursor-123")
            self.assertEqual(loaded.data["ai_requests_today"], 1)

    def test_local_daily_limit_stops_more_reservations(self):
        with tempfile.TemporaryDirectory() as directory:
            state = agent.LocalState(Path(directory) / "state.json")
            state.data["ai_requests_today"] = agent.MAX_AI_REQUESTS_PER_UTC_DAY
            self.assertFalse(state.reserve_ai_attempt())
            self.assertEqual(state.data["ai_requests_today"], agent.MAX_AI_REQUESTS_PER_UTC_DAY)


class InteractiveAgentTests(unittest.TestCase):
    def _create_agent(self, directory):
        api = FakeBotAPI()
        instance = agent.InteractiveAgent(
            token="test-private-token-0123456789",
            owner_id="owner-id-123",
            repo_root=Path(directory),
            state_path=Path(directory) / "state.json",
            api=api,
        )
        return instance, api

    def test_help_command_does_not_call_model(self):
        with tempfile.TemporaryDirectory() as directory:
            instance, api = self._create_agent(directory)
            with patch.object(agent, "ProviderClient") as provider:
                stopped = instance.handle_text("/help")
            self.assertFalse(stopped)
            provider.assert_not_called()
            self.assertTrue(api.sent)
            self.assertIn("/provider groq", api.sent[-1][1])

    def test_unauthorized_message_is_marked_but_never_answered(self):
        with tempfile.TemporaryDirectory() as directory:
            instance, api = self._create_agent(directory)
            update = {
                "type": "NewMessage",
                "chat_id": "someone-else",
                "new_message": {
                    "message_id": "message-unauthorized",
                    "sender_id": "someone-else",
                    "text": "hello",
                },
            }
            self.assertFalse(instance.process_update(update))
            self.assertEqual(api.sent, [])
            self.assertEqual(len(instance.state.data["processed_ids"]), 1)

    def test_duplicate_owner_message_is_processed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            instance, api = self._create_agent(directory)
            update = {
                "type": "NewMessage",
                "chat_id": "owner-id-123",
                "new_message": {
                    "message_id": "message-help",
                    "sender_id": "owner-id-123",
                    "text": "/help",
                },
            }
            instance.process_update(update)
            first_count = len(api.sent)
            instance.process_update(update)
            self.assertEqual(first_count, 1)
            self.assertEqual(len(api.sent), first_count)

    def test_stop_command_does_not_call_model(self):
        with tempfile.TemporaryDirectory() as directory:
            instance, api = self._create_agent(directory)
            with patch.object(agent, "ProviderClient") as provider:
                stopped = instance.handle_text("/stop")
            self.assertTrue(stopped)
            provider.assert_not_called()
            self.assertIn("متوقف", api.sent[-1][1])


if __name__ == "__main__":
    unittest.main()
