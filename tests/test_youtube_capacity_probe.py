from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from agent.brain import youtube_capacity_probe as probe


class YouTubeCapacityProbeTests(unittest.TestCase):
    def test_probe_ladder_is_increasing_and_is_only_a_test_plan(self):
        values = probe.probe_size_ladder_mib()
        self.assertEqual(values, tuple(sorted(set(values))))
        self.assertEqual(values[0], 16)
        self.assertGreaterEqual(values[-1], 1024)
        self.assertIn(2048, values)

    def test_probe_requires_the_registered_test_channel(self):
        self.assertFalse(probe.test_channel_is_authorized(""))
        self.assertFalse(probe.test_channel_is_authorized("not-the-test-channel"))
        self.assertFalse(probe.test_channel_is_authorized("main-channel-guid"))

    def test_api_transport_errors_are_reduced_to_safe_labels(self):
        token = "do-not-print-this-token"
        with patch.object(probe.requests, "post", side_effect=requests.Timeout(token)):
            result = probe._api_post(token, "getMe", {})
        serialized = json.dumps(result)
        self.assertFalse(result["transport_ok"])
        self.assertNotIn(token, serialized)
        self.assertIsNone(result["http_status"])

    def test_probe_rejects_a_size_below_the_minimum_before_running_ffmpeg(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "too-small.mp4"
            with self.assertRaisesRegex(probe.ProbeError, "probe_target_size_invalid"):
                probe._create_sized_mp4(target, 512)

    def test_size_ladder_is_not_written_as_a_production_upload_cap(self):
        self.assertNotIn("rubika_upload_limit_bytes", probe.PROBE_SIZES_MIB.__str__())
        self.assertEqual(probe.MIN_FREE_RESERVE_BYTES, 2 * probe.GIB)


if __name__ == "__main__":
    unittest.main()
