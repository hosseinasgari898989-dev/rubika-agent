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

    def get_updates(self, offset_id=None):
        return {"status": "OK", "data": {"updates": [], "next_offset_id": offset_id}}

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {"status": "OK"}


class FakeLocalModel:
    def __init__(self, *, error=None, answer="پاسخ محلی آزمایشی"):
        self.error = error
        self.answer = answer
        self.health_checks = 0
        self.completions = []

    def check_available(self):
        self.health_checks += 1
        if self.error:
            raise self.error

    def complete(self, messages):
        self.completions.append(messages)
        return self.answer


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
    def _update(self, *, chat_id="owner-chat-123", sender_id="owner-user-456",
                update_type="NewMessage", extra=None):
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

    def test_accepts_only_owner_private_text_with_separate_ids(self):
        update = self._update()
        self.assertEqual(
            agent._owner_private_text(update, "owner-chat-123", "owner-user-456"),
            "/help",
        )

    def test_rejects_other_sender_and_other_chat(self):
        self.assertIsNone(agent._owner_private_text(
            self._update(sender_id="someone-else"), "owner-chat-123", "owner-user-456"
        ))
        self.assertIsNone(agent._owner_private_text(
            self._update(chat_id="group-id-123"), "owner-chat-123", "owner-user-456"
        ))

    def test_rejects_group_channel_and_edited_messages(self):
        self.assertIsNone(agent._owner_private_text(
            self._update(extra={"chat_type": "group"}), "owner-chat-123", "owner-user-456"
        ))
        self.assertIsNone(agent._owner_private_text(
            self._update(update_type="UpdatedMessage"), "owner-chat-123", "owner-user-456"
        ))

    def test_requires_message_identity_to_deduplicate(self):
        self.assertIsNone(agent._message_identity({"type": "NewMessage", "chat_id": "owner-id"}))


class LocalModelClientTests(unittest.TestCase):
    def _completion_response(self, content="سلام؛ پاسخ از مدل محلی"):
        return FakeResponse({"choices": [{"message": {"content": content}}]})

    def test_uses_only_fixed_loopback_and_sends_no_api_key(self):
        client = agent.LocalModelClient(environ={})
        with patch.object(agent.requests, "get", return_value=FakeResponse({"status": "ok"})) as get, \
             patch.object(agent.requests, "post", return_value=self._completion_response()) as post:
            client.check_available()
            answer = client.complete([
                {"role": "system", "content": "system"},
                {"role": "user", "content": "question"},
            ])
        self.assertEqual(answer, "سلام؛ پاسخ از مدل محلی")
        self.assertEqual(get.call_args.args[0], "http://127.0.0.1:8080/health")
        self.assertEqual(post.call_args.args[0],
                         "http://127.0.0.1:8080/v1/chat/completions")
        headers = post.call_args.kwargs["headers"]
        self.assertNotIn("Authorization", headers)
        self.assertEqual(post.call_args.kwargs["json"]["model"], "qwen-local")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(post.call_count, 1)

    def test_unavailable_local_server_returns_safe_error(self):
        client = agent.LocalModelClient(environ={})
        with patch.object(agent.requests, "get", side_effect=requests.ConnectionError()):
            with self.assertRaisesRegex(agent.SafeAgentError, "local_model_not_running"):
                client.check_available()

    def test_timeout_is_not_retried(self):
        client = agent.LocalModelClient(environ={})
        with patch.object(agent.requests, "post", side_effect=requests.Timeout()) as post:
            with self.assertRaisesRegex(agent.SafeAgentError, "local_model_timeout_no_retry"):
                client.complete([{"role": "user", "content": "question"}])
        self.assertEqual(post.call_count, 1)

    def test_rejects_invalid_model_alias(self):
        with self.assertRaisesRegex(agent.SafeAgentError, "local_model_name_invalid"):
            agent.LocalModelClient(environ={"LOCAL_MODEL_NAME": "model name with spaces"})

    def test_rejects_non_successful_model_response(self):
        client = agent.LocalModelClient(environ={})
        with patch.object(agent.requests, "post", return_value=FakeResponse({}, status_code=503)):
            with self.assertRaisesRegex(agent.SafeAgentError, "local_model_server_error"):
                client.complete([{"role": "user", "content": "question"}])


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
            (root / ".env").write_text("LOCAL_KEY=SHOULD_NEVER_APPEAR", encoding="utf-8")
            context = agent.RepositoryContext(root).retrieve("cloud queue worker")
            self.assertIn("agent/demo.py", context)
            self.assertIn("cloud_queue_worker", context)
            self.assertNotIn("SHOULD_NEVER_APPEAR", context)
            self.assertNotIn("private.py", context)
            self.assertNotIn("artifacts/report.md", context)

    def test_secret_like_text_is_redacted_before_context_is_returned(self):
        text = "RUBIKA_TOKEN = 'my-private-token'\nAuthorization: Bearer secret-value"
        clean = agent.redact_sensitive_text(text)
        self.assertNotIn("my-private-token", clean)
        self.assertNotIn("Bearer secret-value", clean)
        self.assertIn("[REDACTED]", clean)


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
            self.assertNotIn("provider", parsed)
            loaded = agent.LocalState(path)
            self.assertEqual(loaded.data["offset_id"], "cursor-123")
            self.assertEqual(loaded.data["ai_requests_today"], 1)

    def test_legacy_provider_setting_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({
                "offset_id": None, "processed_ids": [],
                "usage_day": agent.utc_day(), "ai_requests_today": 0,
                "provider": "groq",
            }), encoding="utf-8")
            state = agent.LocalState(path)
            self.assertNotIn("provider", state.data)
            state.save()
            self.assertNotIn("provider", json.loads(path.read_text(encoding="utf-8")))

    def test_local_daily_limit_stops_more_reservations(self):
        with tempfile.TemporaryDirectory() as directory:
            state = agent.LocalState(Path(directory) / "state.json")
            state.data["ai_requests_today"] = agent.MAX_AI_REQUESTS_PER_UTC_DAY
            self.assertFalse(state.reserve_ai_attempt())
            self.assertEqual(state.data["ai_requests_today"], agent.MAX_AI_REQUESTS_PER_UTC_DAY)


class InteractiveAgentTests(unittest.TestCase):
    def _create_agent(self, directory, model=None):
        api = FakeBotAPI()
        model = model or FakeLocalModel()
        instance = agent.InteractiveAgent(
            token="test-private-token-0123456789",
            owner_chat_id="owner-chat-123",
            owner_user_id="owner-user-456",
            repo_root=Path(directory),
            state_path=Path(directory) / "state.json",
            api=api,
            model_factory=lambda: model,
        )
        return instance, api, model

    def test_help_command_does_not_call_model(self):
        with tempfile.TemporaryDirectory() as directory:
            instance, api, model = self._create_agent(directory)
            stopped = instance.handle_text("/help")
            self.assertFalse(stopped)
            self.assertEqual(model.health_checks, 0)
            self.assertEqual(model.completions, [])
            self.assertTrue(api.sent)
            self.assertIn("/status", api.sent[-1][1])
            self.assertIn("llama.cpp", api.sent[-1][1])

    def test_normal_question_uses_local_model_once(self):
        with tempfile.TemporaryDirectory() as directory:
            instance, api, model = self._create_agent(directory)
            instance.handle_text("سلام، پروژه چه کاری می‌کند؟")
            self.assertEqual(model.health_checks, 1)
            self.assertEqual(len(model.completions), 1)
            self.assertIn("پاسخ محلی آزمایشی", api.sent[-1][1])
            self.assertEqual(instance.state.data["ai_requests_today"], 1)

    def test_local_model_unavailable_does_not_consume_daily_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            model = FakeLocalModel(error=agent.SafeAgentError("local_model_not_running"))
            instance, api, model = self._create_agent(directory, model=model)
            instance.handle_text("سلام")
            self.assertEqual(model.health_checks, 1)
            self.assertEqual(model.completions, [])
            self.assertEqual(instance.state.data["ai_requests_today"], 0)
            self.assertIn("local_model_not_running", api.sent[-1][1])

    def test_unauthorized_message_is_marked_but_never_answered(self):
        with tempfile.TemporaryDirectory() as directory:
            instance, api, _ = self._create_agent(directory)
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
            instance, api, _ = self._create_agent(directory)
            update = {
                "type": "NewMessage",
                "chat_id": "owner-chat-123",
                "new_message": {
                    "message_id": "message-help",
                    "sender_id": "owner-user-456",
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
            instance, api, model = self._create_agent(directory)
            stopped = instance.handle_text("/stop")
            self.assertTrue(stopped)
            self.assertEqual(model.health_checks, 0)
            self.assertIn("متوقف", api.sent[-1][1])

    def test_assistant_is_not_wired_into_cloud_workflows_or_mutation_tools(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "termux" / "interactive_agent.py").read_text(encoding="utf-8")
        for forbidden in (
            "subprocess", "os.system", "RubikaPublisher", "send_video(",
            "os.popen", "eval(", "api.groq.com", "openrouter.ai",
            "GROQ_API_KEY", "OPENROUTER_API_KEY",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        workflows = root / ".github" / "workflows"
        for path in workflows.glob("*.yml"):
            with self.subTest(workflow=path.name):
                self.assertNotIn(
                    "python -m termux.interactive_agent",
                    path.read_text(encoding="utf-8"),
                )
        launcher = (root / "termux" / "interactive-start.sh").read_text(encoding="utf-8")
        self.assertNotIn(r"\${", launcher)
        self.assertIn('CONFIG_FILE="${CONFIG_DIR}/interactive.env"', launcher)


if __name__ == "__main__":
    unittest.main()
