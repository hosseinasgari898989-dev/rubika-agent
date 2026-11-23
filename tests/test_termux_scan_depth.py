import os
import unittest
from unittest.mock import patch

from termux import daily_discovery


class ScanDepthTests(unittest.TestCase):
    def test_default_is_eight(self):
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("TERMUX_SCAN_DEPTH", None)
            self.assertEqual(daily_discovery.scan_depth_value(), 8)

    def test_values_are_clamped_between_eight_and_one_hundred(self):
        for raw, expected in (("8", 8), ("40", 40), ("100", 100), ("500", 100), ("3", 8), ("abc", 8), ("", 8)):
            with self.subTest(raw=raw), patch.dict("os.environ", {"TERMUX_SCAN_DEPTH": raw}):
                self.assertEqual(daily_discovery.scan_depth_value(), expected)


if __name__ == "__main__":
    unittest.main()
