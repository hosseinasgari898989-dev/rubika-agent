import unittest
from unittest.mock import patch

from agent.source import draft_run

from tests.test_source_quiz_templates import CHARACTERS


class DraftRunTests(unittest.TestCase):
    def test_dry_run_does_not_send(self):
        with patch.object(draft_run.wiki_source, "load_characters", return_value=CHARACTERS), \
                patch.object(draft_run, "_send_to_owner") as send:
            code = draft_run.main(["--count", "2"])
        self.assertEqual(code, 0)
        send.assert_not_called()

    def test_send_flag_sends_each_draft_as_its_own_message(self):
        with patch.object(draft_run.wiki_source, "load_characters", return_value=CHARACTERS), \
                patch.object(draft_run, "_send_to_owner", return_value=True) as send:
            code = draft_run.main(["--count", "2", "--send"])
        self.assertEqual(code, 0)
        self.assertEqual(send.call_count, 2)


if __name__ == "__main__":
    unittest.main()
