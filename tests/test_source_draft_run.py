import unittest
from unittest.mock import patch

from agent.source import draft_run

from tests.test_source_quiz_templates import CHARACTERS


class FakeBot:
    def __init__(self, poll_fails=False):
        self.poll_fails = poll_fails
        self.polls = []
        self.texts = []

    def _post(self, method, payload):
        if self.poll_fails:
            raise RuntimeError("poll rejected")
        self.polls.append((method, payload))
        return {"status": "OK"}

    def send_message(self, chat_id, text):
        self.texts.append((chat_id, text))
        return {"status": "OK"}


def _ok(raw):
    return isinstance(raw, dict) and raw.get("status") == "OK"


ENV = {"RUBIKA_TOKEN": "mock-token-never-log", "RUBIKA_OWNER_CHAT_ID": "owner-chat"}


class DraftRunTests(unittest.TestCase):
    def test_dry_run_does_not_send(self):
        with patch.object(draft_run.wiki_source, "load_characters", return_value=CHARACTERS), \
                patch.object(draft_run, "_send_to_owner") as send:
            code = draft_run.main(["--count", "2"])
        self.assertEqual(code, 0)
        send.assert_not_called()

    def test_quiz_poll_is_sent_with_hidden_answer_fields(self):
        bot = FakeBot()
        draft = {"question": "q", "options": ["a", "b", "c"], "answer_index": 2, "url": "https://example.test/x"}
        with patch.dict("os.environ", ENV), patch("agent.brain.owner_notify._make_rubika_bot", return_value=bot), \
                patch("agent.brain.owner_notify._rubika_ok", side_effect=_ok):
            result = draft_run._send_to_owner(draft, "fallback text")
        self.assertEqual(result, "quiz")
        method, payload = bot.polls[0]
        self.assertEqual(method, "sendPoll")
        self.assertEqual(payload["type"], "Quiz")
        self.assertEqual(payload["correct_option_index"], 2)
        self.assertEqual(payload["options"], ["a", "b", "c"])
        self.assertEqual(payload["chat_id"], "owner-chat")
        self.assertEqual(bot.texts, [])

    def test_text_fallback_is_used_when_the_poll_fails(self):
        bot = FakeBot(poll_fails=True)
        draft = {"question": "q", "options": ["a", "b", "c"], "answer_index": 0, "url": "https://example.test/x"}
        with patch.dict("os.environ", ENV), patch("agent.brain.owner_notify._make_rubika_bot", return_value=bot), \
                patch("agent.brain.owner_notify._rubika_ok", side_effect=_ok):
            result = draft_run._send_to_owner(draft, "fallback text")
        self.assertEqual(result, "text")
        self.assertEqual(bot.texts, [("owner-chat", "fallback text")])

    def test_send_flag_reports_counts(self):
        with patch.object(draft_run.wiki_source, "load_characters", return_value=CHARACTERS), \
                patch.object(draft_run, "_send_to_owner", return_value="quiz") as send:
            code = draft_run.main(["--count", "2", "--send"])
        self.assertEqual(code, 0)
        self.assertEqual(send.call_count, 2)


if __name__ == "__main__":
    unittest.main()
