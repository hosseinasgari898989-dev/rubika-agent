from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from agent.brain.mob_x_link_publisher import TEST_CHANNEL_GUID, publish_one


JOB_ID = "job_abcdef1234567890"
EXTERNAL_ID = "2107878755079213476"
PAYLOAD = {
    "item_id": "mob_official:" + EXTERNAL_ID,
    "external_id": EXTERNAL_ID,
    "rubika_file_id": "mock-file-001",
    "caption": "کپشن ساده",
    "review_state": "approved",
}


class FakeClient:
    def __init__(self, claim=None, status_payload=None, status_error=None):
        self.claim = claim or {
            "ok": True,
            "claimed": True,
            "job": {"id": JOB_ID, "type": "mob_x_link", "status": "sending", "result": dict(PAYLOAD)},
        }
        self.status_payload = status_payload or {"ok": True, "capabilities": {"publishing": True}}
        self.status_error = status_error
        self.calls = []

    def rules(self):
        return {"test_channel_only": True, "daily_success_cap": 4, "quiet_hours": {"start": "00:30", "end": "07:00"}}

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/api/status":
            if self.status_error:
                raise self.status_error
            return self.status_payload
        if path == "/api/console/mob-x/publish-claim":
            return self.claim
        return {"ok": True, "status": (payload or {}).get("status")}


class MobXPublisherTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014": "approved",
            "RUBIKA_TOKEN": "mock-rubika-token",
            "RUBIKA_OWNER_CHAT_ID": "owner-private-chat",
            "RUBIKA_CHANNEL_GUID": TEST_CHANNEL_GUID,
            "AGENT_API_BASE_URL": "https://worker.example.invalid",
            "AGENT_API_TOKEN": "mock-worker-token-that-is-long-enough",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_disabled_gate_does_not_claim_or_send(self):
        os.environ["RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014"] = "not-approved"
        client = FakeClient()
        sender = Mock()
        report = publish_one(client=client, sender=sender, notifier=Mock())
        self.assertEqual(report["status"], "idle")
        self.assertFalse(report["publishing_attempted"])
        self.assertEqual(client.calls, [])
        sender.assert_not_called()

    def test_wrong_channel_is_blocked_before_claim(self):
        os.environ["RUBIKA_CHANNEL_GUID"] = "production-channel"
        client = FakeClient()
        sender = Mock()
        report = publish_one(client=client, sender=sender, notifier=Mock())
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["error_code"], "rubika_channel_not_exact_test_target")
        self.assertEqual(client.calls, [])
        sender.assert_not_called()

    def test_missing_test_channel_guard_is_blocked(self):
        client = FakeClient()
        client.rules = lambda: {"test_channel_only": False}
        sender = Mock()
        report = publish_one(client=client, sender=sender, notifier=Mock())
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["error_code"], "console_test_channel_guard_not_enabled")
        self.assertEqual(client.calls, [])
        sender.assert_not_called()

    def test_worker_publishing_false_never_claims_or_sends(self):
        client = FakeClient(status_payload={"ok": True, "capabilities": {"publishing": False}})
        sender = Mock()
        report = publish_one(client=client, sender=sender, notifier=Mock())
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["error_code"], "publishing_disabled")
        self.assertFalse(report["publishing_attempted"])
        self.assertEqual(sum(path == "/api/console/mob-x/publish-claim" for _, path, _ in client.calls), 0)
        sender.assert_not_called()

    def test_status_request_failure_is_fail_closed(self):
        client = FakeClient(status_error=RuntimeError("status unavailable"))
        sender = Mock()
        report = publish_one(client=client, sender=sender, notifier=Mock())
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["error_code"], "publishing_disabled")
        self.assertEqual(sum(path == "/api/console/mob-x/publish-claim" for _, path, _ in client.calls), 0)
        sender.assert_not_called()

    def test_successful_owner_approved_test_send_occurs_once(self):
        client = FakeClient(status_payload={"ok": True, "capabilities": {"publishing": True}})
        sender = Mock(return_value="rubika-message-001")
        report = publish_one(client=client, sender=sender, notifier=Mock())
        sender.assert_called_once_with("mock-rubika-token", TEST_CHANNEL_GUID, "mock-file-001", "کپشن ساده")
        self.assertEqual(report["status"], "sent")
        self.assertTrue(report["publishing_attempted"])
        self.assertEqual(sum(path == "/api/console/mob-x/publish-claim" for _, path, _ in client.calls), 1)
        delivery = [call for call in client.calls if call[1].endswith("/delivery")]
        self.assertEqual(len(delivery), 1)
        self.assertEqual(delivery[0][2], {"status": "sent", "message_id": "rubika-message-001"})

    def test_ambiguous_send_is_recorded_and_not_retried(self):
        client = FakeClient()
        sender = Mock(side_effect=TimeoutError("response lost"))
        notifier = Mock(return_value=True)
        report = publish_one(client=client, sender=sender, notifier=notifier)
        sender.assert_called_once()
        self.assertEqual(report["status"], "uncertain")
        self.assertFalse(report["automatic_retry"])
        notifier.assert_called_once_with(
            "mock-rubika-token", "owner-private-chat", JOB_ID, "rubika_send_outcome_unknown"
        )
        delivery = [call for call in client.calls if call[1].endswith("/delivery")]
        self.assertEqual(delivery[0][2], {"status": "uncertain", "error_code": "rubika_send_outcome_unknown"})

    def test_success_without_message_receipt_is_uncertain_not_retried(self):
        client = FakeClient()
        sender = Mock(return_value=None)
        notifier = Mock(return_value=True)
        report = publish_one(client=client, sender=sender, notifier=notifier)
        sender.assert_called_once()
        self.assertEqual(report["status"], "uncertain")
        self.assertEqual(report["error_code"], "rubika_send_receipt_missing")
        notifier.assert_called_once()

    def test_no_approved_job_never_attempts_publication(self):
        client = FakeClient({"ok": True, "claimed": False, "reason": "no_owner_approved_mob_x_job"})
        sender = Mock()
        report = publish_one(client=client, sender=sender, notifier=Mock())
        self.assertEqual(report["status"], "idle")
        self.assertFalse(report["publishing_attempted"])
        sender.assert_not_called()

    def test_invalid_approved_payload_is_failed_without_channel_send(self):
        client = FakeClient()
        client.claim["job"]["result"] = {"caption": ""}
        sender = Mock()
        report = publish_one(client=client, sender=sender, notifier=Mock())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_code"], "mob_x_approved_payload_incomplete")
        self.assertFalse(report["publishing_attempted"])
        sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
