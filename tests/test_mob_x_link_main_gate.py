import unittest
from unittest.mock import patch

from agent.brain import mob_x_link_publisher, mob_x_rubika, test_channel

TEST_GUID = "fixture-test-guid"
MAIN_GUID = "main-guid"
JOB_ID = "job_aaaaaaaa"


def _env(gate, main=MAIN_GUID, channel=MAIN_GUID):
    return {
        "RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014": "approved",
        "RUBIKA_AGENT_MOB_X_NEWS_MAIN_CHANNEL_GATE": gate,
        "RUBIKA_AGENT_TIKTOK_EDIT_QUEUE_PRODUCTION_GATE": "",
        "RUBIKA_MAIN_CHANNEL_GUID": main,
        "RUBIKA_CHANNEL_GUID": channel,
        "RUBIKA_TOKEN": "tok",
        "RUBIKA_OWNER_CHAT_ID": "owner-chat",
    }


class FakeBot:
    def __init__(self, **kwargs):
        self.posts = []

    def _post(self, method, payload):
        self.posts.append((method, payload))
        return {"status": "OK", "data": {"message_id": "m1"}}


class FakeWorker:
    def request(self, method, path, payload=None):
        if path == "/api/status":
            return {
                "publishing_control_available": True,
                "publishing_paused": False,
                "capabilities": {"publishing": True},
            }
        if path == "/api/console/mob-x/publish-claim":
            return {
                "claimed": True,
                "job": {
                    "id": JOB_ID,
                    "result": {
                        "rubika_file_id": "file-1",
                        "caption": "caption",
                        "item_id": "item-1",
                        "external_id": "2107878755079213476",
                    },
                },
            }
        return {}

    def rules(self):
        return {"test_channel_only": False}


class MobLinkMainGateTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(
            test_channel,
            "is_test_channel_guid",
            side_effect=lambda value: str(value or "").strip() == TEST_GUID,
        )
        guard.start()
        self.addCleanup(guard.stop)

    def test_sender_allows_test_always_and_main_only_with_open_gate(self):
        bots = []

        def factory(**kwargs):
            bot = FakeBot(**kwargs)
            bots.append(bot)
            return bot

        with patch.dict("os.environ", _env("open")):
            self.assertEqual(
                mob_x_rubika.send_test_channel_once("tok", MAIN_GUID, "file-1", "caption", bot_factory=factory), "m1"
            )
        self.assertEqual(bots[-1].posts[0][1]["chat_id"], MAIN_GUID)
        with patch.dict("os.environ", _env("")):
            with self.assertRaises(RuntimeError):
                mob_x_rubika.send_test_channel_once("tok", MAIN_GUID, "file-1", "caption", bot_factory=factory)
            self.assertEqual(
                mob_x_rubika.send_test_channel_once("tok", TEST_GUID, "file-1", "caption", bot_factory=factory), "m1"
            )

    def test_publish_one_goes_to_main_when_gate_open_and_is_blocked_when_closed(self):
        sent = []

        def sender(token, channel, file_id, caption):
            sent.append(channel)
            return "m1"

        with patch.dict("os.environ", _env("open")):
            report = mob_x_link_publisher.publish_one(client=FakeWorker(), sender=sender)
        self.assertEqual(report["status"], "sent")
        self.assertEqual(report["channel"], "main")
        self.assertEqual(sent, [MAIN_GUID])

        sent.clear()
        with patch.dict("os.environ", _env("")):
            report = mob_x_link_publisher.publish_one(client=FakeWorker(), sender=sender)
        self.assertEqual(report["error_code"], "rubika_channel_not_exact_test_target")
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
