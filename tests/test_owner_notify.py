from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from agent.brain import owner_notify


class FakeRubikaBot:
    def __init__(self, send_result=None, edit_result=None, send_exception=None):
        self.send_result = send_result or {"status": "OK", "data": {"message_id": "rubika-msg-123"}}
        self.edit_result = edit_result or {"status": "OK"}
        self.send_exception = send_exception
        self.sent = []
        self.edited = []
        self.posts = []

    def _post(self, method, payload):
        self.posts.append((method, payload))
        return {"status": "OK"}

    def send_message(self, *, chat_id, text):
        self.sent.append({"chat_id": chat_id, "text": text})
        if self.send_exception is not None:
            raise self.send_exception
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
        self.workflow_failures = patch.object(owner_notify, "collect_workflow_failures", return_value={
            "termux": 0, "daily_queue": 0, "mob_x": 0, "deploy": 0,
        })
        self.workflow_failures.start()
        self.addCleanup(self.workflow_failures.stop)

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
        self.assertEqual(bot.posts, [])
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
        self.assertEqual(bot.posts, [])
        self.assertEqual(len(bot.edited), 1)
        self.assertEqual(bot.edited[0]["message_id"], "rubika-existing")

    def test_invalid_access_edit_sends_one_replacement_and_persists_new_id(self):
        bot = FakeRubikaBot(edit_result={"status": "INVALID_ACCESS"})
        state = digest_state(message_id="rubika-inaccessible", message_body="stale digest")
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

        self.assertTrue(result["sent"])
        self.assertTrue(result["replaced"])
        self.assertEqual(len(bot.edited), 1)
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.posts, [])
        self.assertEqual(bot.sent[0]["chat_id"], "owner-chat-id")
        self.assertEqual(commits[-1]["message_id"], "rubika-msg-123")
        self.assertFalse(commits[-1]["message_creation_uncertain"])
        self.assertEqual(commits[-1]["message_body"], owner_notify.render_daily_digest(state))

    def test_ambiguous_replacement_send_is_marked_uncertain_and_not_retried(self):
        bot = FakeRubikaBot(
            edit_result={"status": "INVALID_ACCESS"},
            send_exception=TimeoutError("simulated timeout"),
        )
        state = digest_state(message_id="rubika-inaccessible", message_body="stale digest")
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
        self.assertEqual(result["reason"], "rubika_digest_replacement_send_uncertain")
        self.assertEqual(len(bot.edited), 1)
        self.assertEqual(len(bot.sent), 1)
        self.assertIsNone(commits[-1]["message_id"])
        self.assertTrue(commits[-1]["message_creation_uncertain"])


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
             "source": "code_ai_groq", "title": "AI repair call failed",
             "detail": "error_code=provider_unavailable https://secret.example/?token=hidden"},
        ])
        state["counts"] = {"info": 0, "success": 0, "error": 0, "ai_success": 0, "ai_error": 1}
        rendered = owner_notify.render_daily_digest(state)
        self.assertIn("خطاها: 1", rendered)
        self.assertIn("AI repair call failed", rendered)
        self.assertNotIn("https://secret.example", rendered)
        self.assertNotIn("token=hidden", rendered)



    def test_workflow_failure_monitor_counts_consecutive_runs_and_ignores_pull_requests(self):
        fixtures = {
            "termux-docker-discovery.yml": [
                {"status": "completed", "conclusion": "failure", "event": "pull_request", "head_branch": "feature", "created_at": "2026-11-03T09:00:00Z"},
                {"status": "completed", "conclusion": "failure", "event": "schedule", "head_branch": "main", "created_at": "2026-11-03T08:00:00Z"},
                {"status": "completed", "conclusion": "timed_out", "event": "schedule", "head_branch": "main", "created_at": "2026-11-02T20:00:00Z"},
                {"status": "completed", "conclusion": "success", "event": "schedule", "head_branch": "main", "created_at": "2026-11-02T09:00:00Z"},
                {"status": "completed", "conclusion": "failure", "event": "schedule", "head_branch": "main", "created_at": "2026-11-01T20:00:00Z"},
            ],
            "daily-queue-processor.yml": [
                {"status": "completed", "conclusion": "failure", "event": "schedule", "head_branch": "main", "created_at": "2026-11-02T16:00:00Z"},
                {"status": "completed", "conclusion": "success", "event": "schedule", "head_branch": "main", "created_at": "2026-11-01T16:00:00Z"},
            ],
            "mob-x-link-processor.yml": [
                {"status": "completed", "conclusion": "success", "event": "schedule", "head_branch": "main", "created_at": "2026-11-03T08:00:00Z"},
                {"status": "completed", "conclusion": "failure", "event": "schedule", "head_branch": "main", "created_at": "2026-11-03T07:55:00Z"},
            ],
            "deploy-worker.yml": [
                {"status": "completed", "conclusion": "failure", "event": "push", "head_branch": "main", "created_at": "2026-11-03T08:00:00Z"},
                {"status": "completed", "conclusion": "timed_out", "event": "push", "head_branch": "main", "created_at": "2026-11-03T07:00:00Z"},
                {"status": "completed", "conclusion": "success", "event": "push", "head_branch": "main", "created_at": "2026-11-03T06:00:00Z"},
            ],
        }
        with patch.object(owner_notify, "_github_workflow_runs", side_effect=lambda name: fixtures[name]):
            self.assertEqual(owner_notify.collect_workflow_failures(), {
                "termux": 2, "daily_queue": 1, "mob_x": 0, "deploy": 2,
            })

    def test_workflow_monitor_reports_unavailable_run_history_as_unknown(self):
        with patch.object(owner_notify, "_github_workflow_runs", return_value=None):
            self.assertEqual(owner_notify.collect_workflow_failures(), {
                "termux": None, "daily_queue": None, "mob_x": None, "deploy": None,
            })

    def test_digest_health_section_warns_at_threshold_and_reports_unreadable_quotas_once(self):
        state = digest_state()
        state["health"] = {
            "d1_size_bytes": 250 * 1024 * 1024,
            "daily_d1_writes": 70_000,
            "daily_d1_reads": None,
            "daily_worker_requests": 70_000,
            "workers_ai_neurons": None,
            "mega_used_bytes": None,
        }
        state["workflow_failures"] = {
            "termux": 2, "daily_queue": 1, "mob_x": 0, "deploy": 2,
        }
        rendered = owner_notify.render_daily_digest(state)
        self.assertIn("هشدار PV در اندازهٔ D1", rendered)
        self.assertIn("D1 writes/day: 70,000/100,000 rows (70.0%)", rendered)
        self.assertIn("⚠️ Worker requests/day: 70,000/100,000 requests (70.0%)", rendered)
        self.assertIn("D1 reads/day: نامشخص", rendered)
        self.assertIn("Workers AI neurons/day: نامشخص", rendered)
        self.assertIn("Mega: مصرف و فضای آزاد قابل‌خواندن نیست", rendered)
        self.assertEqual(rendered.count("Mega: مصرف و فضای آزاد قابل‌خواندن نیست"), 1)
        self.assertIn("Termux=2 | Daily queue=1 | Mob X=0 | Deploy=2", rendered)
        self.assertIn("صف، dedupe و ارسال هرگز محدود نمی‌شوند", rendered)

    def test_workflow_failed_notification_includes_sanitized_error_detail(self):
        with (
            patch("sys.argv", [
                "owner_notify", "--event", "workflow_failed",
                "--workflow", "Mob X link processor",
                "--reason", "mob_x_speech_gate_failed",
                "--detail", "RuntimeError: model download failed https://huggingface.co/model?token=hidden-value",
            ]),
            patch.object(owner_notify, "send_owner_notification", return_value={"sent": True}) as notify,
        ):
            self.assertEqual(owner_notify.main(), 0)
        message = notify.call_args.args[0]
        self.assertIn("error_detail: RuntimeError: model download failed", message)
        self.assertNotIn("https://huggingface.co", message)
        self.assertNotIn("token=hidden-value", message)


if __name__ == "__main__":
    unittest.main()
