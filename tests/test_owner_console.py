from __future__ import annotations

import hashlib
import json
import unittest
import urllib.error
from unittest.mock import patch

from agent.brain import owner_console as console


class FakeHTTPResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def read(self, limit=-1):
        return self._payload[:limit] if limit >= 0 else self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeOpener:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.requests = []

    def open(self, request, timeout=0):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        if not self.responses:
            raise AssertionError("Unexpected HTTP request in offline test")
        return self.responses.pop(0)


class FakeBot:
    def __init__(self, updates=None, send_error=None):
        self.updates = updates or {"status": "OK", "data": {"updates": [], "next_offset_id": "next-cursor"}}
        self.send_error = send_error
        self.sent = []
        self.sent_keypads = []
        self.edits = []
        self.get_updates_offsets = []

    def get_updates(self, offset_id):
        self.get_updates_offsets.append(offset_id)
        return self.updates

    def send_message(self, chat_id, text, inline_keypad=None):
        self.sent.append((chat_id, text))
        self.sent_keypads.append(inline_keypad)
        if self.send_error:
            raise self.send_error
        return {"status": "OK"}

    def edit_inline_keypad(self, chat_id, message_id, inline_keypad):
        self.edits.append((chat_id, message_id, inline_keypad))
        return {"status": "OK"}


class FakeControl:
    def __init__(self):
        self.offset = None
        self.claims = []
        self.finished = []
        self.status_calls = 0
        self.queue_calls = 0
        self.ai_calls = []
        self._claimed = set()
        self.status_payload = {
            "ok": True, "database": "connected", "agent": {"enabled": 0},
            "item_counts": [{"status": "ready", "total": 2}],
            "recent_runs": [{"id": "run-12345678", "status": "ok"}],
            "capabilities": {"publishing": False}, "publishing": False,
        }
        self.queue_payload = {
            "ok": True,
            "queue_counts": {
                "queued": 2, "processing": 1, "sent": 3, "failed": 0,
                "uncertain": 1, "skipped": 2,
            },
            "sent_today": 1,
            "item_counts": {"ready": 2, "sent": 3},
            "publishing": False,
        }

    def get_state(self):
        return {"ok": True, "offset_id": self.offset}

    def set_offset(self, offset_id):
        self.offset = offset_id
        return {"ok": True, "offset_saved": True}

    def claim(self, update_id, chat_hash, command):
        self.claims.append((update_id, chat_hash, command))
        if update_id in self._claimed:
            return {"ok": True, "claimed": False, "duplicate": True, "status": "answered"}
        self._claimed.add(update_id)
        return {"ok": True, "claimed": True, "update_id": update_id}

    def finish(self, update_id, status, result_code):
        self.finished.append((update_id, status, result_code))
        return {"ok": True, "status": status}

    def status(self):
        self.status_calls += 1
        return self.status_payload

    def queue_summary(self):
        self.queue_calls += 1
        return self.queue_payload

    def ai_status(self, provider):
        self.ai_calls.append(provider)
        return {
            "ok": True, "provider": provider, "ready": True, "status": "ready",
            "calls_used": 2, "daily_limit": 6, "remaining": 4,
            "cooldown_active": False, "last_error_code": None,
        }


def update(*, text="/status", chat_id="b0owner-chat-123", sender_id="owner-user-456",
           message_id="message-001", update_type="NewMessage", extra=None):
    value = {
        "type": update_type,
        "chat_id": chat_id,
        "new_message": {
            "message_id": message_id,
            "sender_id": sender_id,
            "text": text,
        },
    }
    if extra:
        value.update(extra)
    return value

def callback(*, button_id="mob_post_from_x", chat_id="b0owner-chat-123",
             sender_id="owner-user-456", message_id="menu-message-001",
             start_id="callback-start-001", update_type="ReceiveQuery", extra=None):
    value = {
        "type": update_type,
        "inline_message": {
            "message_id": message_id,
            "chat_id": chat_id,
            "sender_id": sender_id,
            "text": "button caption must not be logged",
            "aux_data": {"button_id": button_id, "start_id": start_id},
        },
    }
    if extra:
        value.update(extra)
    return value


class CommandParserTests(unittest.TestCase):
    def test_only_allowlisted_commands_are_recognized(self):
        self.assertEqual(console.parse_command("/status").audit_name, "status")
        self.assertEqual(console.parse_command("/queue").audit_name, "queue")
        self.assertEqual(console.parse_command("/report").name, "report")
        self.assertEqual(console.parse_command("/ai-status").audit_name, "ai-status")
        self.assertEqual(console.parse_command("/ai-status GROQ").audit_name, "ai-status:groq")
        self.assertEqual(console.parse_command("/ai-status openrouter").provider, "openrouter")
        self.assertEqual(console.parse_command("/edit src/index.js").name, "unknown")
        self.assertEqual(console.parse_command("please run a command").name, "unknown")
        self.assertEqual(console.parse_command("/ai-status some-other-provider").name, "unknown")
        self.assertEqual(console.parse_command("/status argument").name, "unknown")

    def test_private_message_gate_requires_private_configured_chat_not_sender_id(self):
        accepted = console._owner_private_text(update(text="/status"), "b0owner-chat-123")
        self.assertEqual(accepted, "/status")
        self.assertEqual(
            console._owner_private_text(update(sender_id="different-sender"), "b0owner-chat-123"),
            "/status",
        )
        # An explicit private-chat type is also accepted if the configured chat matches.
        self.assertEqual(
            console._owner_private_text(
                update(chat_id="configured-private-chat", extra={"chat_type": "private"}),
                "configured-private-chat",
            ),
            "/status",
        )
        self.assertIsNone(console._owner_private_text(update(chat_id="other-chat"), "b0owner-chat-123"))
        self.assertIsNone(console._owner_private_text(update(chat_id="g0group-chat-123"), "b0owner-chat-123"))
        self.assertIsNone(console._owner_private_text(update(chat_id="c0channel-chat-123"), "b0owner-chat-123"))
        self.assertIsNone(console._owner_private_text(
            update(extra={"chat_type": "group"}), "b0owner-chat-123"
        ))
        self.assertIsNone(console._owner_private_text(
            update(extra={"chat_type": "channel"}), "b0owner-chat-123"
        ))
        self.assertIsNone(console._owner_private_text(
            update(update_type="UpdatedMessage"), "b0owner-chat-123"
        ))
        bot_update = update(text="/status", sender_id="anything")
        bot_update["new_message"]["sender_type"] = "Bot"
        self.assertIsNone(console._owner_private_text(bot_update, "b0owner-chat-123"))
        self.assertIsNone(console._owner_private_text(
            update(extra={"chat_type": "unknown"}), "b0owner-chat-123"
        ))

    def test_message_identity_is_stable_and_does_not_include_raw_id_in_storage(self):
        item = update()
        first = console._message_identity(item)
        second = console._message_identity(item)
        expected = hashlib.sha256(b"b0owner-chat-123|message-001").hexdigest()
        self.assertEqual(first, expected)
        self.assertEqual(first, second)
        self.assertNotIn("b0owner-chat-123", first)
        self.assertEqual(len(first), 64)


class FormattingTests(unittest.TestCase):
    def test_status_and_queue_summaries_are_bounded_and_non_mutating(self):
        status = console.format_status({
            "ok": True, "database": "connected", "agent": {"enabled": 0},
            "publishing": False, "capabilities": {"publishing": False},
            "item_counts": [{"status": "ready", "total": 3}],
            "recent_runs": [{"id": "run-12345678", "status": "ok"}],
        })
        self.assertIn("پایگاه داده: متصل", status)
        self.assertIn("انتشار از این API: غیرفعال", status)
        self.assertIn("ready: 3", status)
        queue = console.format_queue({
            "ok": True, "queue_counts": {"queued": 4, "processing": 1, "sent": 3},
            "sent_today": 1, "item_counts": {"ready": 2}, "publishing": False,
        })
        self.assertIn("در انتظار: 4", queue)
        self.assertIn("هیچ موردی از صف", queue)
        self.assertLessEqual(len(status), console.MAX_REPLY_CHARS)
        self.assertLessEqual(len(queue), console.MAX_REPLY_CHARS)

    def test_ai_report_only_shows_allowlisted_status_fields(self):
        rendered = console.format_ai_status("groq", {
            "ok": True, "ready": False, "status": "cooldown_active",
            "calls_used": 3, "daily_limit": 18, "remaining": 15,
            "cooldown_active": True, "last_error_code": "rate_limited",
            "api_key": "must-not-show",
        })
        self.assertIn("3/18", rendered)
        self.assertIn("rate_limited", rendered)
        self.assertNotIn("must-not-show", rendered)


class HTTPClientTests(unittest.TestCase):
    def test_worker_client_uses_fixed_https_host_and_no_redirects(self):
        opener = FakeOpener([FakeHTTPResponse({"ok": True, "database": "connected"})])
        client = console.ControlWorker(
            "https://rubika-agent.h98609294.workers.dev", "t" * 40, opener=opener
        )
        self.assertEqual(client.status()["database"], "connected")
        request_obj, timeout = opener.requests[0]
        self.assertEqual(request_obj.full_url, "https://rubika-agent.h98609294.workers.dev/api/status")
        self.assertEqual(request_obj.get_method(), "GET")
        self.assertNotIn("Authorization", request_obj.headers)
        self.assertEqual(timeout, 10)
        with self.assertRaisesRegex(console.OwnerConsoleError, "control_api_url_invalid"):
            console.ControlWorker("https://attacker.example", "t" * 40, opener=FakeOpener())
        with self.assertRaisesRegex(console.OwnerConsoleError, "control_api_path_not_allowed"):
            client._request("GET", "/api/brain/anything-secret")

    def test_ai_status_query_is_allowlisted(self):
        opener = FakeOpener([FakeHTTPResponse({"ok": True, "status": "ready"})])
        client = console.ControlWorker(
            "https://rubika-agent.h98609294.workers.dev", "t" * 40, opener=opener
        )
        client.ai_status("openrouter")
        self.assertIn("provider=openrouter", opener.requests[0][0].full_url)
        with self.assertRaisesRegex(console.OwnerConsoleError, "code_ai_provider_not_enabled"):
            client.ai_status("unknown")

    def test_worker_client_rejects_arbitrary_queries_and_paths(self):
        client = console.ControlWorker(
            "https://rubika-agent.h98609294.workers.dev", "t" * 40, opener=FakeOpener()
        )
        with self.assertRaisesRegex(console.OwnerConsoleError, "control_api_query_not_allowed"):
            client._request("GET", "/api/brain/owner-console/state?offset=private")
        with self.assertRaisesRegex(console.OwnerConsoleError, "control_api_provider_invalid"):
            client._request("GET", "/api/brain/ai-provider/status?provider=groq&provider=openrouter")
        with self.assertRaisesRegex(console.OwnerConsoleError, "control_api_path_not_allowed"):
            client._request("GET", "/api/brain/owner-console/not-allowlisted")
        with self.assertRaisesRegex(console.OwnerConsoleError, "control_api_method_not_allowed"):
            client._request("GET", "/api/brain/owner-console/claim")

    def test_rubika_transport_errors_never_reveal_token(self):
        token = "private-rubika-token-1234567890"
        api = console.RubikaBotAPI(token, opener=FakeOpener(error=TimeoutError()))
        with self.assertRaises(console.OwnerConsoleError) as caught:
            api.get_updates(None)
        self.assertEqual(caught.exception.code, "rubika_network_result_unknown")
        self.assertNotIn(token, str(caught.exception))

    def test_github_run_formatter_never_accepts_raw_logs(self):
        rendered = console.format_github_runs([{
            "id": 1234, "name": "Cloud bootstrap checks",
            "branch": "main", "status": "completed", "conclusion": "failure",
        }])
        self.assertIn("failure", rendered)
        self.assertIn("run 1234", rendered)
        self.assertNotIn("token", rendered.lower())

    def test_report_prioritizes_recent_ci_failures(self):
        rendered = console.format_github_runs([
            {"id": 10, "name": "Latest green", "branch": "main", "status": "completed", "conclusion": "success"},
            {"id": 9, "name": "Another green", "branch": "main", "status": "completed", "conclusion": "success"},
            {"id": 8, "name": "Earlier failure", "branch": "feature", "status": "completed", "conclusion": "failure"},
            {"id": 7, "name": "Older run", "branch": "main", "status": "completed", "conclusion": "success"},
        ])
        self.assertIn("خطاهای اخیر", rendered)
        self.assertIn("run 8", rendered)
        self.assertIn("run 10", rendered)
        self.assertLess(rendered.index("run 8"), rendered.index("run 10"))


class OperationsConsoleTests(unittest.TestCase):
    def make_console(self, bot=None, control=None, github_runs=None):
        api = bot or FakeBot()
        worker = control or FakeControl()
        instance = console.OperationsConsole(
            bot=api,
            control=worker,
            owner_chat_id="b0owner-chat-123",
            github_runs=github_runs or (lambda: []),
        )
        return instance, api, worker

    def test_help_command_is_read_only_and_does_not_call_worker(self):
        instance, bot, control = self.make_console()
        result = instance.process_update(update(text="/help"))
        self.assertEqual(result, "answered")
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("/queue", bot.sent[0][1])
        self.assertEqual(control.status_calls, 0)
        self.assertEqual(control.queue_calls, 0)
        self.assertEqual(control.ai_calls, [])
        self.assertEqual(control.finished[0][1], "answered")

    def test_queue_command_uses_only_read_only_summary(self):
        instance, bot, control = self.make_console()
        self.assertEqual(instance.process_update(update(text="/queue")), "answered")
        self.assertEqual(control.queue_calls, 1)
        self.assertEqual(control.status_calls, 0)
        self.assertIn("در انتظار: 2", bot.sent[-1][1])
        self.assertNotIn("send_video", bot.sent[-1][1])

    def test_ai_status_queries_each_provider_without_inference(self):
        instance, bot, control = self.make_console()
        self.assertEqual(instance.process_update(update(text="/ai-status")), "answered")
        self.assertEqual(control.ai_calls, list(console.PROVIDERS))
        self.assertIn("GROQ", bot.sent[-1][1])
        self.assertEqual(control.queue_calls, 0)

    def test_report_gathers_status_queue_ai_and_latest_ci(self):
        instance, bot, control = self.make_console(
            github_runs=lambda: [{"id": 999, "name": "CI", "branch": "main",
                                  "status": "completed", "conclusion": "success"}]
        )
        self.assertEqual(instance.process_update(update(text="/report")), "answered")
        self.assertEqual(control.status_calls, 1)
        self.assertEqual(control.queue_calls, 1)
        self.assertEqual(control.ai_calls, list(console.PROVIDERS))
        self.assertIn("run 999", bot.sent[-1][1])
        self.assertIn("گزارش Rubika-Agent", bot.sent[-1][1])
        self.assertLessEqual(len(bot.sent[-1][1]), console.MAX_REPLY_CHARS)

    def test_unapproved_commands_are_not_executed(self):
        instance, bot, control = self.make_console()
        result = instance.process_update(update(text="/edit src/index.js"))
        self.assertEqual(result, "answered")
        self.assertIn("پشتیبانی نمی‌شود", bot.sent[-1][1])
        self.assertEqual(control.status_calls, 0)
        self.assertEqual(control.queue_calls, 0)
        self.assertEqual(control.ai_calls, [])

    def test_duplicate_update_never_sends_a_second_reply(self):
        instance, bot, control = self.make_console()
        incoming = update(text="/status")
        self.assertEqual(instance.process_update(incoming), "answered")
        self.assertEqual(instance.process_update(incoming), "duplicate")
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(len(control.claims), 2)

    def test_non_owner_or_bot_authored_updates_are_ignored_without_claim_or_reply(self):
        instance, bot, control = self.make_console()
        other_chat = update(text="/status", chat_id="other-chat", message_id="other-chat-message")
        self.assertEqual(instance.process_update(other_chat), "ignored")
        group_chat = update(text="/status", chat_id="g0group-chat-123", message_id="group-chat-message")
        self.assertEqual(instance.process_update(group_chat), "ignored")
        bot_message = update(text="/status", message_id="bot-authored-message")
        bot_message["new_message"]["sender_type"] = "Bot"
        self.assertEqual(instance.process_update(bot_message), "ignored")
        self.assertEqual(bot.sent, [])
        self.assertEqual(control.claims, [])

    def test_ambiguous_send_is_recorded_uncertain_and_never_retried(self):
        instance, bot, control = self.make_console(bot=FakeBot(send_error=console.OwnerConsoleError("rubika_network_result_unknown")))
        incoming = update(text="/status")
        self.assertEqual(instance.process_update(incoming), "uncertain")
        self.assertEqual(instance.process_update(incoming), "duplicate")
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(control.finished[0][1], "uncertain")

    def test_poll_once_persists_cursor_after_batch_and_skips_other_users(self):
        api = FakeBot(updates={
            "status": "OK",
            "data": {
                "updates": [
                    update(text="/status"),
                    update(text="/status", chat_id="other-chat", sender_id="other"),
                ],
                "next_offset_id": "cursor-next",
            },
        })
        control = FakeControl()
        control.offset = console.encode_console_state("cursor-existing", console.CONSOLE_IDLE)
        instance, bot, control = self.make_console(bot=api, control=control)
        stats = instance.poll_once()
        self.assertEqual(stats["answered"], 1)
        self.assertEqual(stats["ignored"], 1)
        self.assertFalse(stats["bootstrapped"])
        self.assertTrue(stats["offset_advanced"])
        self.assertEqual(console.decode_console_state(control.offset), ("cursor-next", console.CONSOLE_IDLE))
        self.assertEqual(bot.get_updates_offsets, ["cursor-existing"])
        self.assertEqual(len(bot.sent), 1)

    def test_first_poll_bootstraps_cursor_without_replying_to_old_messages(self):
        api = FakeBot(updates={
            "status": "OK",
            "data": {
                "updates": [update(text="/status"), update(text="/queue")],
                "next_offset_id": "cursor-next",
            },
        })
        instance, bot, control = self.make_console(bot=api)
        stats = instance.poll_once()
        self.assertTrue(stats["bootstrapped"])
        self.assertEqual(stats["updates_received"], 2)
        self.assertEqual(stats["ignored"], 2)
        self.assertEqual(stats["answered"], 0)
        self.assertEqual(bot.sent, [])
        self.assertEqual(control.claims, [])
        self.assertEqual(console.decode_console_state(control.offset), ("cursor-next", console.CONSOLE_IDLE))
        self.assertTrue(stats["offset_advanced"])

    def test_poll_once_accepts_configured_private_chat_without_sender_id_match(self):
        api = FakeBot(updates={
            "status": "OK",
            "data": {
                "updates": [update(text="/status", sender_id="different-sender")],
                "next_offset_id": "cursor-next",
            },
        })
        control = FakeControl()
        control.offset = "cursor-existing"
        instance, bot, control = self.make_console(bot=api, control=control)
        stats = instance.poll_once()
        self.assertEqual(stats["answered"], 1)
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(len(control.claims), 1)
        self.assertEqual(console.decode_console_state(control.offset), ("cursor-next", console.CONSOLE_IDLE))

    def test_start_and_menu_send_exact_inline_keypad_payload(self):
        instance, bot, control = self.make_console()
        self.assertEqual(instance.process_update(update(text="/start")), "answered")
        self.assertEqual(bot.sent_keypads[-1], console.menu_inline_keypad())
        button = bot.sent_keypads[-1]["rows"][0]["buttons"][0]
        self.assertEqual(button, {
            "id": "mob_post_from_x", "type": "Simple", "button_text": "📰 خبر Mob از لینک X",
        })
        self.assertEqual(control.claims[-1][2], "help")

    def test_menu_alias_sends_the_same_keypad(self):
        instance, bot, _ = self.make_console()
        self.assertEqual(instance.process_update(update(text="/menu")), "answered")
        self.assertEqual(bot.sent_keypads[-1], console.menu_inline_keypad())

    def test_receive_query_callback_enters_persistent_waiting_state(self):
        api = FakeBot(updates={
            "status": "OK",
            "data": {"updates": [callback()], "next_offset_id": "cursor-after-callback"},
        })
        control = FakeControl()
        control.offset = "cursor-before-callback"
        instance, bot, control = self.make_console(bot=api, control=control)
        stats = instance.poll_once()
        self.assertEqual(stats["answered"], 1)
        self.assertEqual(instance.conversation_state, console.CONSOLE_WAITING_FOR_LINK)
        self.assertEqual(console.decode_console_state(control.offset), ("cursor-after-callback", console.CONSOLE_WAITING_FOR_LINK))
        self.assertEqual(bot.sent_keypads[-1], console.waiting_inline_keypad())
        self.assertEqual(control.claims[0][2], "unknown")
        self.assertEqual(bot.edits[0][1], "menu-message-001")

    def test_waiting_link_accepts_x_and_twitter_status_and_resets_state(self):
        instance, bot, _ = self.make_console()
        self.assertEqual(instance.process_update(callback()), "answered")
        self.assertEqual(instance.conversation_state, console.CONSOLE_WAITING_FOR_LINK)
        self.assertEqual(instance.process_update(update(
            text="Please check https://twitter.com/MobFan/status/12345678901234567?s=20",
            message_id="message-link-002",
        )), "answered")
        self.assertEqual(instance.conversation_state, console.CONSOLE_IDLE)
        self.assertIn("https://x.com/MobFan/status/12345678901234567", bot.sent[-1][1])
        self.assertIn("پردازش لینک در به‌روزرسانی بعدی", bot.sent[-1][1])
        self.assertIn("دانلود، ترجمه یا انتشار انجام نشده", bot.sent[-1][1])
        self.assertEqual(bot.sent_keypads[-1], console.menu_inline_keypad())

    def test_waiting_state_keeps_cancel_for_non_status_text(self):
        instance, bot, _ = self.make_console()
        instance.process_update(callback())
        self.assertEqual(instance.process_update(update(text="Hello, not a status URL", message_id="message-nonlink")), "answered")
        self.assertEqual(instance.conversation_state, console.CONSOLE_WAITING_FOR_LINK)
        self.assertIn("لینک معتبر", bot.sent[-1][1])
        self.assertEqual(bot.sent_keypads[-1], console.waiting_inline_keypad())

    def test_cancel_callback_returns_to_menu(self):
        instance, bot, _ = self.make_console()
        instance.process_update(callback())
        self.assertEqual(instance.conversation_state, console.CONSOLE_WAITING_FOR_LINK)
        self.assertEqual(instance.process_update(callback(
            button_id="owner_console_cancel", message_id="cancel-menu-002", start_id="cancel-2"
        )), "answered")
        self.assertEqual(instance.conversation_state, console.CONSOLE_IDLE)
        self.assertEqual(bot.sent_keypads[-1], console.menu_inline_keypad())

    def test_callback_double_tap_is_claimed_once(self):
        instance, bot, control = self.make_console()
        incoming = callback()
        self.assertEqual(instance.process_update(incoming), "answered")
        self.assertEqual(instance.process_update(incoming), "duplicate")
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(len(control.claims), 2)

    def test_x_status_url_parser_accepts_only_canonical_status_paths(self):
        self.assertEqual(
            console.parse_x_status_url("post https://x.com/MobFan/status/12345678901234567?t=abc"),
            "https://x.com/MobFan/status/12345678901234567",
        )
        self.assertEqual(
            console.parse_x_status_url("https://twitter.com/name_2/status/22345678901234567"),
            "https://x.com/name_2/status/22345678901234567",
        )
        self.assertIsNone(console.parse_x_status_url("https://x.com/MobFan"))
        self.assertIsNone(console.parse_x_status_url("https://x.com/MobFan/likes"))
        self.assertIsNone(console.parse_x_status_url("https://notx.com/MobFan/status/12345678901234567"))
        self.assertIsNone(console.parse_x_status_url("https://x.com/MobFan/status/1234"))

    def test_update_diagnostics_log_only_last_four_type_and_chat_id_characters(self):
        incoming = update(text="PRIVATE MESSAGE BODY")
        with patch("builtins.print") as printed:
            console._log_update_diagnostic(incoming)
        rendered = " ".join(str(arg) for call in printed.call_args_list for arg in call.args)
        payload = json.loads(printed.call_args.args[0])
        self.assertEqual(payload, {"type_tail": "sage", "chat_id_tail": "-123"})
        self.assertNotIn("NewMessage", rendered)
        self.assertNotIn("b0owner-chat-123", rendered)
        self.assertNotIn("owner-user-456", rendered)
        self.assertNotIn("PRIVATE MESSAGE BODY", rendered)

    def test_unknown_update_shape_is_ignored_without_body_diagnostics(self):
        instance, bot, control = self.make_console()
        incoming = {"type": "UnexpectedUpdate", "text": "PRIVATE BODY MUST NOT LOG"}
        with patch("builtins.print") as printed:
            self.assertEqual(instance.process_update(incoming), "ignored")
        rendered = " ".join(str(arg) for call in printed.call_args_list for arg in call.args)
        self.assertIn('"type_tail":"date"', rendered)
        self.assertNotIn("UnexpectedUpdate", rendered)
        self.assertNotIn("PRIVATE BODY MUST NOT LOG", rendered)
        self.assertEqual(set(json.loads(printed.call_args.args[0])), {"type_tail", "chat_id_tail"})
        self.assertEqual(bot.sent, [])
        self.assertEqual(control.claims, [])

    def test_callback_authorization_requires_private_owner_chat_not_sender_id(self):
        instance, bot, control = self.make_console()
        self.assertEqual(instance.process_update(callback(chat_id="wrong-chat")), "ignored")
        self.assertEqual(instance.process_update(callback(chat_id="g0group-chat-123", message_id="menu-group")), "ignored")
        self.assertEqual(instance.process_update(callback(sender_id="different-sender", message_id="menu-2")), "answered")
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(len(control.claims), 1)

    def test_rubika_api_v3_inline_payload_shapes_match_installed_rubka(self):
        opener = FakeOpener([
            FakeHTTPResponse({"status": "OK"}),
            FakeHTTPResponse({"status": "OK"}),
            FakeHTTPResponse({"status": "OK"}),
        ])
        api = console.RubikaBotAPI("r" * 32, opener=opener)
        keypad = console.menu_inline_keypad()
        api.send_message("owner-chat", "Menu", inline_keypad=keypad)
        api.edit_message_text("owner-chat", "message-1", "Edited")
        api.edit_inline_keypad("owner-chat", "message-1", {"rows": []})
        methods = [request.full_url.rsplit("/", 1)[-1] for request, _ in opener.requests]
        self.assertEqual(methods, ["sendMessage", "editMessageText", "editMessageKeypad"])
        payloads = [json.loads(request.data.decode("utf-8")) for request, _ in opener.requests]
        self.assertEqual(payloads[0]["inline_keypad"], keypad)
        self.assertEqual(payloads[1], {"chat_id": "owner-chat", "message_id": "message-1", "text": "Edited"})
        self.assertEqual(payloads[2], {"chat_id": "owner-chat", "message_id": "message-1", "inline_keypad": {"rows": []}})


class EnvironmentConfigTests(unittest.TestCase):
    def test_required_environment_uses_only_existing_owner_chat_setting(self):
        env = {
            "RUBIKA_TOKEN": "r" * 32,
            "RUBIKA_OWNER_CHAT_ID": "b0owner-chat-123",
            "AGENT_API_TOKEN": "a" * 40,
        }
        with patch.dict(console.os.environ, env, clear=True):
            _, owner_chat_id, _, control_url = console._required_env()
        self.assertEqual(owner_chat_id, "b0owner-chat-123")
        self.assertEqual(control_url, console.DEFAULT_CONTROL_URL)


if __name__ == "__main__":
    unittest.main()