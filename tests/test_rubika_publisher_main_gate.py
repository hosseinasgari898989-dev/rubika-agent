import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain import rubika_publisher, test_channel


class FakeBot:
    def __init__(self, **kwargs):
        self.posts = []

    def _post(self, method, payload):
        self.posts.append((method, payload))
        return {"status": "OK", "data": {"message_id": "1"}}

    def get_upload_url(self, kind):
        return "https://upload.invalid/x"

    def upload_media_file(self, url, name, path):
        return "file-1"


def _env(gate, main):
    return {
        "RUBIKA_AGENT_MOB_X_NEWS_MAIN_CHANNEL_GATE": gate,
        "RUBIKA_MAIN_CHANNEL_GUID": main,
    }


class MainDestinationGuardTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(
            test_channel,
            "is_test_channel_guid",
            side_effect=lambda value: str(value or "").strip() == "fixture-test-guid",
        )
        guard.start()
        self.addCleanup(guard.stop)

    def test_test_destination_always_allowed(self):
        with patch.dict("os.environ", _env("", "")):
            self.assertEqual(rubika_publisher.assert_test_channel_destination("fixture-test-guid"), "fixture-test-guid")

    def test_main_blocked_when_gate_closed_or_unrecognized(self):
        for gate in ("", "true", "approved", "OPEN"):
            with self.subTest(gate=gate), patch.dict("os.environ", _env(gate, "main-guid")):
                with self.assertRaises(rubika_publisher.RubikaDestinationGuardError):
                    rubika_publisher.assert_test_channel_destination("main-guid")

    def test_open_gate_allows_only_exact_main_destination(self):
        with patch.dict("os.environ", _env("open", "main-guid")):
            self.assertEqual(rubika_publisher.assert_test_channel_destination("main-guid"), "main-guid")
            with self.assertRaises(rubika_publisher.RubikaDestinationGuardError):
                rubika_publisher.assert_test_channel_destination("other-guid")
            with self.assertRaises(rubika_publisher.RubikaDestinationGuardError):
                rubika_publisher.assert_test_channel_destination("")

    def test_open_gate_with_missing_or_test_pinned_main_secret_fails_closed(self):
        for main in ("", "fixture-test-guid"):
            with self.subTest(main_present=bool(main)), patch.dict("os.environ", _env("open", main)):
                with self.assertRaises(rubika_publisher.RubikaDestinationGuardError):
                    rubika_publisher.assert_test_channel_destination("main-guid")

    def test_open_gate_sends_media_to_exact_main_destination(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", _env("open", "main-guid")):
            media = Path(folder) / "a.jpg"
            media.write_bytes(b"x")
            bot = FakeBot()
            publisher = rubika_publisher.RubikaPublisher("tok", "main-guid", bot_factory=lambda **kw: bot)
            publisher.send_media(media, "image", "caption")
        self.assertEqual(bot.posts[0][0], "sendFile")
        self.assertEqual(bot.posts[0][1]["chat_id"], "main-guid")

    def test_closed_gate_never_posts_to_main(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", _env("", "main-guid")):
            media = Path(folder) / "a.jpg"
            media.write_bytes(b"x")
            bot = FakeBot()
            publisher = rubika_publisher.RubikaPublisher("tok", "main-guid", bot_factory=lambda **kw: bot)
            with self.assertRaises(rubika_publisher.RubikaDestinationGuardError):
                publisher.send_media(media, "image", "caption")
        self.assertEqual(bot.posts, [])


if __name__ == "__main__":
    unittest.main()
