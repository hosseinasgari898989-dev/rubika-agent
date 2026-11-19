import json
import unittest

from agent.brain import channel_read_probe as probe

TEST_GUID = "c0TESTGUIDTESTGUIDTESTGUID0000"
MAIN_GUID = "c0MAINGUIDMAINGUIDMAINGUID0000"


class FakeBot:
    def __init__(self):
        self.calls = []
        self.sends = 0

    def get_chat(self, chat_id):
        self.calls.append(chat_id)
        if chat_id == TEST_GUID:
            return {"status": "OK", "data": {"chat": {"chat_id": chat_id, "chat_type": "Channel"}}}
        return {"status": "INVALID_INPUT", "dev_message": "bad chat_id " + MAIN_GUID}

    def send_message(self, *_a, **_k):
        self.sends += 1


class ChannelReadProbeTests(unittest.TestCase):
    def test_ok_and_invalid_are_sanitized_and_nothing_is_sent(self):
        bot = FakeBot()
        test_out = probe._probe(bot, "test", TEST_GUID, "tok-secret")
        main_out = probe._probe(bot, "main", MAIN_GUID, "tok-secret")
        self.assertTrue(test_out["ok"])
        self.assertEqual(test_out["chat_type"], "Channel")
        self.assertTrue(test_out["echoed_id_matches"])
        self.assertFalse(main_out["ok"])
        self.assertEqual(main_out["status"], "INVALID_INPUT")
        encoded = json.dumps([test_out, main_out])
        self.assertNotIn(TEST_GUID, encoded)
        self.assertNotIn(MAIN_GUID, encoded)
        self.assertNotIn("tok-secret", encoded)
        self.assertEqual(bot.sends, 0)

    def test_missing_guid_makes_no_call(self):
        bot = FakeBot()
        out = probe._probe(bot, "main", "", "tok")
        self.assertFalse(out["guid_present"])
        self.assertEqual(bot.calls, [])


if __name__ == "__main__":
    unittest.main()
