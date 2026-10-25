from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain import e2e_from_report
from agent.brain.rubika_publisher import RubikaDestinationGuardError


class E2EDestinationGuardTests(unittest.TestCase):
    def test_non_test_destination_is_rejected_before_cloud_or_storage_setup(self):
        with (
            patch.dict(os.environ, {"RUBIKA_CHANNEL_GUID": "different-channel"}),
            patch.object(e2e_from_report, "CloudState") as cloud_state,
            patch.object(e2e_from_report, "MegaStorage") as mega_storage,
        ):
            with self.assertRaisesRegex(
                RubikaDestinationGuardError,
                "rubika_destination_not_test_channel",
            ):
                e2e_from_report.run_one(
                    Path("does-not-exist.json"),
                    "tiktok",
                    Path("unused-report.json"),
                )
        cloud_state.assert_not_called()
        mega_storage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
