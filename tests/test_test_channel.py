from __future__ import annotations

import hashlib
import unittest
from unittest.mock import patch

from agent.brain import test_channel


class TestChannelHashTests(unittest.TestCase):
    def test_accepts_value_matching_registered_sha256(self):
        fixture = "synthetic-test-channel-guid"
        digest = hashlib.sha256(fixture.encode("utf-8")).hexdigest()
        with patch.object(test_channel, "EXPECTED_TEST_CHANNEL_GUID_SHA256", digest):
            self.assertTrue(test_channel.is_test_channel_guid(fixture))
            self.assertEqual(test_channel.assert_test_channel_guid(fixture), fixture)

    def test_rejects_wrong_empty_and_whitespace_only_values(self):
        fixture = "synthetic-test-channel-guid"
        digest = hashlib.sha256(fixture.encode("utf-8")).hexdigest()
        with patch.object(test_channel, "EXPECTED_TEST_CHANNEL_GUID_SHA256", digest):
            for candidate in ("different-channel", "", "   ", None):
                with self.subTest(candidate=candidate):
                    self.assertFalse(test_channel.is_test_channel_guid(candidate))
                    with self.assertRaisesRegex(ValueError, "rubika_destination_not_test_channel"):
                        test_channel.assert_test_channel_guid(candidate)


if __name__ == "__main__":
    unittest.main()
