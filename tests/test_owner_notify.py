from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from agent.brain import owner_notify


class FakeRubikaBot:
    def __init__(self, send_result=None, edit_result=None):
        self.send_result = send_result or {"status": "OK", "data": {"message_id": "rubika-msg-123"}}
        self.edit_result = edit_result or {"status": "OK"}
        self.sent = []
        self.edited = []

    def send_message(self, *, chat_id, text):
        self.sent.append({"chat_id": chat_id, "text": text})
        return self.send_result

    def edit_message_text(self, *, chat_id, message_id, text):
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "text": text})
        return self.edit_result


def digest_state(*, message_id=None, message_body="", events=None, uncertain=False):
    return {
        "window_id": "a" * 32,
        "window_started_at": "2026-10-16T07:00:00.000Z",
        "window_ends_at": "2026-10-17T07:00:00.000Z",
        "message_id": message_id,
        "message_body": message_body,
        "message_creation_uncertain": uncertain,
        "counts": {"info": 1, "success": 0, "error": 1, "ai_success": 0, "ai_error": 0},
        "events": events or [{
            "id": 1,
            "occurred_at": "2026-10-16T07:01:00.000Z",
            "category": "error",
            "source": "queue_worker",
            "title": "Pipeline failed",
            "detail": "error_code=staging_failed",
        }],
    }


class OwnerNotifyTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AGENT_API_BASE_URL": "https://worker.example.invalid",
            "AGENT_API_TOKEN": "test-agent-api-token-with-at-least-thirty-two-characters",
            "RUBIKA_TOKEN": "test-rubika-token",
            "RUBIKA_OWNER_CHAT_ID": "owner-chat-id",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_first_event_creates_one_message_and_persists_message_id(self):
        bot = FakeRubikaBot()
        calls = []

        def worker_post(path, payload):
            calls.append((path, payload))
            if path.endswith("/events"):
                return {"ok": True, "window_id": "a" * 32}
            if path.endswith("/claim"):
                return {"ok": True, "claimed": True, "lock_token": "b" * 32,
                        "state": digest_state()}
            if path.endswith("/commit"):
                return {"ok": True, "message_id": payload["message_id"]}
            raise AssertionError(path)

        with (
            patch.object(owner_notify, "_worker_post", side_effect=worker_post),
            patch.object(owner_notify, "_make_rubika_bot", return_value=bot),
        ):
            result = owner_notify.send_owner_notification(
                "🚨 Queue worker failed\nerror_code=staging_failed",
                category="error",
                source="queue_worker",
            )

        self.assertTrue(result["sent"])
        self.assertTrue(result["created"])
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.edited, [])
        self.assertEqual(bot.sent[0]["chat_id"], "owner-chat-id")
        commit = next(payload for path, payload in calls if path.endswith("/commit"))
        self.assertEqual(commit["message_id"], "rubika-msg-123")
        self.assertFalse(commit["message_creation_uncertain"])

    def test_existing_message_is_edited_instead_of_sending_another(self):
        bot = FakeRubikaBot()
        state = digest_state(message_id="rubika-existing", message_body="old body")

        def worker_post(path, payload):
            if path.endswith("/events"):
                return {"ok": True, "window_id": "a" * 32}
            if path.endswith("/claim"):
                return {"ok": True, "claimed": True, "lock_token": "b" * 32,
                        "state": state}
            if path.endswith("/commit"):
                return {"ok": True}
            raise AssertionError(path)

        with (
            patch.object(owner_notify, "_worker_post", side_effect=worker_post),
            patch.object(owner_notify, "_make_rubika_bot", return_value=bot),
        ):
            result = owner_notify.send_owner_notification(
                "✅ New successful run\ncompleted",
                category="success",
                source="daily_worker",
            )

        self.assertTrue(result["sent"])
        self.assertTrue(result["updated"])
        self.assertEqual(bot.sent, [])
        self.assertEqual(len(bot.edited), 1)
        self.assertEqual(bot.edited[0]["message_id"], "rubika-existing")

    def test_invalid_access_edit_creates_replacement_digest_and_persists_new_id(self):
        bot = FakeRubikaBot(edit_result={"status": "INVALID_ACCESS"})
        state = digest_state(message_id="rubika-inaccessible", message_body="old body")
        commits = []

        def worker_post(path, payload):
            if path.endswith("/events"):
                return {"ok": True, "window_id": "a" * 32}
            if path.endswith("/claim"):
                return {"ok": True, "claimed": True, "lock_token": "b" * 32,
                        "state": state}
            if path.endswith("/commit"):
                commits.append(payload)
                return {"ok": True, "message_id": payload["message_id"]}
            raise AssertionError(path)

        with (
            patch.object(owner_notify, "_worker_post", side_effect=worker_post),
            patch.object(owner_notify, "_make_rubika_bot", return_value=bot),
        ):
            result = owner_notify.send_owner_notification(
                "✅ New successful run\\ncompleted",
                category="success",
                source="daily_worker",
            )

        self.assertTrue(result["sent"])
        self.assertTrue(result["created"])
        self.assertTrue(result["replaced_inaccessible"])
        self.assertEqual(len(bot.edited), 1)
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.sent[0]["chat_id"], "owner-chat-id")
        self.assertEqual(commits[-1]["message_id"], "rubika-msg-123")
        self.assertNotEqual(commits[-1]["message_body"], "old body")
        self.assertFalse(commits[-1]["message_creation_uncertain"])

    def test_uncertain_initial_send_does_not_create_duplicates(self):
        bot = FakeRubikaBot()
        state = digest_state(message_id=None, message_body="unknown", uncertain=True)
        commits = []

        def worker_post(path, payload):
            if path.endswith("/claim"):
                return {"ok": True, "claimed": True, "lock_token": "b" * 32,
                        "state": state}
            if path.endswith("/commit"):
                commits.append(payload)
                return {"ok": True}
            raise AssertionError(path)

        with (
            patch.object(owner_notify, "_worker_post", side_effect=worker_post),
            patch.object(owner_notify, "_make_rubika_bot", return_value=bot),
        ):
            result = owner_notify.refresh_daily_digest()

        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "digest_message_creation_uncertain")
        self.assertEqual(bot.sent, [])
        self.assertEqual(bot.edited, [])
        self.assertEqual(len(commits), 1)
        self.assertTrue(commits[0]["message_creation_uncertain"])

    def test_identical_digest_is_not_edited_repeatedly(self):
        bot = FakeRubikaBot()
        state = digest_state(message_id="rubika-existing")
        state["message_body"] = owner_notify.render_daily_digest(state)

        def worker_post(path, payload):
            if path.endswith("/claim"):
                return {"ok": True, "claimed": True, "lock_token": "b" * 32,
                        "state": state}
            if path.endswith("/commit"):
                return {"ok": True}
            raise AssertionError(path)

        with (
            patch.object(owner_notify, "_worker_post", side_effect=worker_post),
            patch.object(owner_notify, "_make_rubika_bot", return_value=bot),
        ):
            result = owner_notify.refresh_daily_digest()

        self.assertTrue(result["sent"])
        self.assertFalse(result["updated"])
        self.assertEqual(bot.sent, [])
        self.assertEqual(bot.edited, [])

    def test_digest_redacts_urls_and_counts_ai_failures(self):
        state = digest_state(events=[
            {"id": 1, "occurred_at": "2026-10-16T08:00:00Z", "category": "ai_error",
             "source": "code_ai_github_models", "title": "AI repair call failed",
             "detail": "error_code=provider_unavailable https://secret.example/?token=hidden"},
        ])
        state["counts"] = {"info": 0, "success": 0, "error": 0, "ai_success": 0, "ai_error": 1}
        rendered = owner_notify.render_daily_digest(state)
        self.assertIn("خطاها: 1", rendered)
        self.assertIn("AI repair call failed", rendered)
        self.assertNotIn("https://secret.example", rendered)
        self.assertNotIn("token=hidden", rendered)


if __name__ == "__main__":
    unittest.main()
