import os
import unittest
from unittest.mock import patch

from termux import daily_discovery


class IgnoreCutoffFlagTests(unittest.TestCase):
    def test_flag_is_true_only_for_the_word_true(self):
        for value, expected in (("true", True), ("TRUE", True), (" true ", True), ("", False), ("false", False), ("1", False), ("yes", False)):
            with self.subTest(value=value), patch.dict("os.environ", {"TERMUX_IGNORE_SOURCE_CREATED_AT": value}):
                self.assertEqual(daily_discovery.ignore_source_cutoff_enabled(), expected)

    def test_unset_defaults_to_false(self):
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("TERMUX_IGNORE_SOURCE_CREATED_AT", None)
            self.assertFalse(daily_discovery.ignore_source_cutoff_enabled())


if __name__ == "__main__":
    unittest.main()
