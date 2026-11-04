from __future__ import annotations

import logging
import unittest

from agent.brain.delivery import deliver_with_retry


class DeliveryRetentionTests(unittest.TestCase):
    def test_success_deletes_local_copy_but_does_not_delete_remote_staging_copy(self):
        local = []
        remote = []
        result = deliver_with_retry(
            item_id="tiktok:test-item",
            send=lambda: {"status": "OK", "data": {"message_id": "test-message"}},
            record_attempt=lambda *_args: None,
            delete_local=lambda: local.append("deleted"),
            max_attempts=1,
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(local, ["deleted"])
        self.assertEqual(remote, [])
        self.assertEqual(result["cleanup"], "local_complete_remote_retained")

    def test_root_message_id_shape_avoids_sdk_message_id_keyerror(self):
        result = deliver_with_retry(
            item_id="tiktok:test-item",
            send=lambda: {"status": "OK", "message_id": "root-message"},
            record_attempt=lambda *_args: None,
            delete_local=lambda: None,
            max_attempts=1,
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["message_id"], "root-message")

    def test_nested_rubika_message_shape_is_parsed(self):
        result = deliver_with_retry(
            item_id="tiktok:test-item",
            send=lambda: {"status": "OK", "data": {"new_message": {"message_id": "nested-message"}}},
            record_attempt=lambda *_args: None,
            delete_local=lambda: None,
            max_attempts=1,
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["message_id"], "nested-message")

    def test_success_without_any_receipt_remains_uncertain_and_is_not_retried(self):
        attempts = []
        deleted = []
        result = deliver_with_retry(
            item_id="tiktok:test-item",
            send=lambda: {"status": "OK", "data": {"status_det": "OK"}},
            record_attempt=lambda number, status, code: attempts.append((number, status, code)),
            delete_local=lambda: deleted.append(True),
            max_attempts=3,
            sleeper=lambda *_args: self.fail("uncertain delivery must never retry"),
        )
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["error"], "delivery_receipt_missing")
        self.assertEqual(attempts, [(1, "started", None), (1, "uncertain", "delivery_receipt_missing")])
        self.assertEqual(deleted, [])

    def test_missing_keys_never_raise_keyerror(self):
        for response in (None, {}, {"status": "OK"}, {"data": {}}, {"status": "OK", "data": []}):
            with self.subTest(response_type=type(response).__name__):
                result = deliver_with_retry(
                    item_id="tiktok:test-item",
                    send=lambda response=response: response,
                    record_attempt=lambda *_args: None,
                    delete_local=lambda: None,
                    max_attempts=1,
                )
                self.assertEqual(result["status"], "uncertain")

    def test_logs_only_present_key_names_and_never_secret_or_destination_values(self):
        secret = "TOP_SECRET_VALUE"
        destination = "RAW_TEST_CHANNEL_VALUE"
        with self.assertLogs("agent.brain.delivery", level="WARNING") as captured:
            result = deliver_with_retry(
                item_id="tiktok:test-item",
                send=lambda: {
                    "status": "OK",
                    "data": {"access_token": secret, "chat_id": destination},
                },
                record_attempt=lambda *_args: None,
                delete_local=lambda: self.fail("missing receipt must retain local file"),
                max_attempts=1,
            )
        joined = "\n".join(captured.output)
        self.assertEqual(result["status"], "uncertain")
        self.assertIn("response", joined)
        self.assertIn("data", joined)
        self.assertIn("access_token", joined)
        self.assertIn("chat_id", joined)
        self.assertNotIn(secret, joined)
        self.assertNotIn(destination, joined)

    def test_failed_and_uncertain_sends_keep_local_copy(self):
        for response in ({"status": "ERROR"}, {}):
            local = []
            result = deliver_with_retry(
                item_id="tiktok:test-item",
                send=lambda response=response: response,
                record_attempt=lambda *_args: None,
                delete_local=lambda: local.append("deleted"),
                max_attempts=1,
            )
            self.assertIn(result["status"], ("failed", "uncertain"))
            self.assertEqual(local, [])


if __name__ == "__main__":
    unittest.main()
