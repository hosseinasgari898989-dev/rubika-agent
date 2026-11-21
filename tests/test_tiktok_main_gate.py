import unittest
from unittest.mock import patch

from agent.brain import rubika_publisher, test_channel


def _env(tiktok_gate, main, destination="", mob_gate=""):
    return {
        "RUBIKA_AGENT_MOB_X_NEWS_MAIN_CHANNEL_GATE": mob_gate,
        "RUBIKA_AGENT_TIKTOK_EDIT_QUEUE_PRODUCTION_GATE": tiktok_gate,
        "RUBIKA_MAIN_CHANNEL_GUID": main,
        "RUBIKA_CHANNEL_GUID": destination,
    }


class TikTokMainGateTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(
            test_channel,
            "is_test_channel_guid",
            side_effect=lambda value: str(value or "").strip() == "fixture-test-guid",
        )
        guard.start()
        self.addCleanup(guard.stop)

    def test_open_tiktok_gate_allows_only_exact_main(self):
        with patch.dict("os.environ", _env("open", "main-guid", "main-guid")):
            self.assertEqual(rubika_publisher.assert_test_channel_destination("main-guid"), "main-guid")
            with self.assertRaises(rubika_publisher.RubikaDestinationGuardError):
                rubika_publisher.assert_test_channel_destination("other-guid")

    def test_closed_or_unrecognized_gate_blocks_main(self):
        for gate in ("", "true", "approved", "OPEN"):
            with self.subTest(gate=gate), patch.dict("os.environ", _env(gate, "main-guid", "main-guid")):
                with self.assertRaises(rubika_publisher.RubikaDestinationGuardError):
                    rubika_publisher.assert_test_channel_destination("main-guid")

    def test_missing_or_test_pinned_main_secret_fails_closed(self):
        for main in ("", "fixture-test-guid"):
            with self.subTest(main_present=bool(main)), patch.dict("os.environ", _env("open", main, "main-guid")):
                with self.assertRaises(rubika_publisher.RubikaDestinationGuardError):
                    rubika_publisher.assert_test_channel_destination("main-guid")

    def test_mob_gate_still_works_independently(self):
        with patch.dict("os.environ", _env("", "main-guid", "main-guid", mob_gate="open")):
            self.assertEqual(rubika_publisher.assert_test_channel_destination("main-guid"), "main-guid")


if __name__ == "__main__":
    unittest.main()
