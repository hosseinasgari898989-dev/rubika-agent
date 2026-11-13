from __future__ import annotations

import unittest

from agent.brain.mob_x_media_cleanup import cleanup_terminal_media, eligible_terminal_media_key


class FakeWorker:
    def __init__(self, jobs_by_status):
        self.jobs_by_status = jobs_by_status
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(path).query)
        status = query["status"][0]
        limit = int(query["limit"][0])
        offset = int(query["offset"][0])
        rows = self.jobs_by_status.get(status, [])
        return {"ok": True, "items": rows[offset:offset + limit]}


class FakeStorage:
    def __init__(self, names):
        self.names = set(names)
        self.deleted = []

    def list_directory(self, relative_dir):
        self.listed_directory = relative_dir
        return sorted(self.names)

    def delete(self, remote_path):
        self.deleted.append(remote_path)
        self.names.remove(remote_path.rsplit("/", 1)[-1])


def job(job_id, status, key, **result):
    payload = dict(result)
    if key is not None:
        payload["storage_key"] = key
    return {"id": job_id, "type": "mob_x_link", "status": status, "result": payload}


class MobXTerminalMediaCleanupTests(unittest.TestCase):
    SENT = "/Rubika-Agent/official/mob_x_2107878755079213476_0123456789ab.mp4"
    REJECTED = "/Rubika-Agent/official/mob_x_2107878755079213477_abcdef012345.jpg"
    LEGACY = "/Rubika-Agent/official/mob_x_media.mp4"

    def test_only_confirmed_sent_rejected_and_pre_publish_failed_jobs_are_eligible(self):
        self.assertEqual(eligible_terminal_media_key(job(
            "job_sent_12345678", "sent", self.SENT,
            delivery_status="sent", rubika_message_id="message-1",
        )), self.SENT)
        self.assertEqual(eligible_terminal_media_key(job(
            "job_reject_123456", "rejected", self.REJECTED, publishing_attempted=False,
        )), self.REJECTED)
        self.assertIsNone(eligible_terminal_media_key(job(
            "job_failed_123456", "failed", self.SENT, publishing_attempted=True,
        )))
        self.assertIsNone(eligible_terminal_media_key(job(
            "job_sent_unknown", "sent", self.SENT, delivery_status="uncertain",
            rubika_message_id="message-1",
        )))
        self.assertIsNone(eligible_terminal_media_key(job(
            "job_uncertain_123", "uncertain", self.SENT, publishing_attempted=True,
            delivery_status="uncertain", rubika_message_id="message-1",
        )))
        self.assertIsNone(eligible_terminal_media_key(job(
            "job_ready_123456", "ready_for_review", self.SENT, publishing_attempted=False,
        )))
        self.assertIsNone(eligible_terminal_media_key(job(
            "job_legacy_123456", "sent", self.LEGACY,
            delivery_status="sent", rubika_message_id="message-1",
        )))
        self.assertIsNone(eligible_terminal_media_key(job(
            "job_outside_123456", "sent",
            "/Elsewhere/official/mob_x_2107878755079213476_0123456789ab.mp4",
            delivery_status="sent", rubika_message_id="message-1",
        )))

    def test_cleanup_paginates_reads_and_deletes_only_existing_unique_terminal_paths(self):
        worker = FakeWorker({
            "sent": [
                job("job_sent_00000001", "sent", self.SENT,
                    delivery_status="sent", rubika_message_id="message-1"),
                job("job_sent_bad00001", "sent", self.SENT,
                    delivery_status="uncertain", rubika_message_id="message-bad"),
            ],
            "rejected": [
                job("job_rejected_0000001", "rejected", self.REJECTED, publishing_attempted=False),
            ],
            "failed": [
                job("job_failed_try0001", "failed", self.SENT, publishing_attempted=True),
            ],
        })
        storage = FakeStorage([self.SENT.rsplit("/", 1)[-1], self.REJECTED.rsplit("/", 1)[-1], "mob_x_media.mp4"])
        report = cleanup_terminal_media(worker, storage, page_size=2)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["deleted_count"], 2)
        self.assertEqual(report["d1_writes"], 0)
        self.assertEqual(storage.listed_directory, "official")
        self.assertEqual(set(storage.deleted), {self.SENT, self.REJECTED})
        self.assertIn("mob_x_media.mp4", storage.names)
        self.assertTrue(worker.calls)
        self.assertTrue(all(method == "GET" and payload is None for method, _path, payload in worker.calls))
        self.assertTrue(any("offset=0" in path for _method, path, _payload in worker.calls))

    def test_missing_file_is_idempotent_and_no_uncertain_status_is_fetched(self):
        worker = FakeWorker({
            "sent": [job("job_sent_00000001", "sent", self.SENT,
                delivery_status="sent", rubika_message_id="message-1")],
            "rejected": [],
            "failed": [],
        })
        storage = FakeStorage([])
        report = cleanup_terminal_media(worker, storage)
        self.assertEqual(report["deleted_count"], 0)
        self.assertEqual(report["already_missing_count"], 1)
        self.assertEqual(storage.deleted, [])
        self.assertFalse(any("status=uncertain" in path for _m, path, _p in worker.calls))


if __name__ == "__main__":
    unittest.main()
