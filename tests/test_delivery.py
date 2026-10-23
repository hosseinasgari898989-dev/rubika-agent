from __future__ import annotations
import unittest
from agent.brain.delivery import deliver_with_retry


class DeliveryRetentionTests(unittest.TestCase):
    def test_success_deletes_local_copy_but_does_not_delete_remote_staging_copy(self):
        local=[]; remote=[]
        result=deliver_with_retry(item_id="tiktok:7616247520657771796",
            send=lambda: {"status":"OK","data":{"message_id":"test-message"}},
            record_attempt=lambda *_args: None,
            delete_local=lambda: local.append("deleted"),
            max_attempts=1)
        self.assertEqual(result["status"],"sent")
        self.assertEqual(local,["deleted"])
        self.assertEqual(remote,[])
        self.assertEqual(result["cleanup"],"local_complete_remote_retained")

    def test_failed_and_uncertain_sends_keep_local_copy(self):
        for response in ({"status":"ERROR"},{}):
            local=[]
            result=deliver_with_retry(item_id="tiktok:7616247520657771796",
                send=lambda response=response: response,
                record_attempt=lambda *_args: None, delete_local=lambda: local.append("deleted"), max_attempts=1)
            self.assertIn(result["status"],("failed","uncertain"))
            self.assertEqual(local,[])


if __name__=="__main__": unittest.main()
