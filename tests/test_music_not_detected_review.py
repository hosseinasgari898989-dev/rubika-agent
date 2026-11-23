import unittest
from unittest.mock import patch

from agent.brain.cloud_state import CloudState

ITEM = "tiktok:7700342245823761694"


class MusicNotDetectedReviewTests(unittest.TestCase):
    def _cloud(self):
        return CloudState("https://example.invalid", token="mock-token-never-log", timeout_seconds=5)

    def test_valid_no_music_review_is_posted(self):
        for probability in (0.0619, 0.0, None):
            with self.subTest(probability=probability), patch.object(CloudState, "_request", return_value={"ok": True}) as request:
                self._cloud().mark_tiktok_owner_review(
                    ITEM, reason="music_not_detected", has_audio=True,
                    music_probability=probability, has_music=False,
                )
            body = request.call_args.args[2]
            self.assertEqual(body["reason"], "music_not_detected")
            self.assertIs(body["has_music"], False)
            self.assertIs(body["has_audio"], True)

    def test_invalid_combinations_are_rejected(self):
        cases = ({"has_music": None}, {"has_music": True}, {"has_music": False, "music_probability": 0.3},
                 {"has_music": False, "music_probability": -0.1})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), patch.object(CloudState, "_request", return_value={"ok": True}) as request:
                with self.assertRaises(ValueError):
                    self._cloud().mark_tiktok_owner_review(ITEM, reason="music_not_detected", has_audio=True, **kwargs)
                request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
