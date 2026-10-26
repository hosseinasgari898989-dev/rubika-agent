from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MobXDiscoveryProbeWorkflowTests(unittest.TestCase):
    def test_production_publisher_uses_one_invocation_and_keeps_guards(self):
        workflow = (ROOT / ".github" / "workflows" / "mob-x-news-production.yml").read_text(encoding="utf-8")
        command = "python -m agent.brain.mob_official_trial"
        self.assertEqual(workflow.count(command), 1)
        self.assertNotIn("--published-within-hours", workflow)
        self.assertNotIn("for attempt in $(seq 1 12)", workflow)
        self.assertIn("vars.RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014 == 'approved'", workflow)
        self.assertIn("target_channel_guid_sha256", workflow)
        self.assertIn("0c939688dd5536447389134ba514e9353a6ef97214de3c4f848adb53e1cd4ab8", workflow)

    def test_probe_is_manual_only_read_only_and_uploads_expected_artifact(self):
        workflow = (ROOT / ".github" / "workflows" / "mob-x-discovery-probe.yml").read_text(encoding="utf-8")
        trigger = workflow.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        for forbidden in ("\n  push:", "\n  schedule:", "\n  workflow_run:", "\n  pull_request:"):
            self.assertNotIn(forbidden, trigger)

        self.assertIn("discover_official_x_posts(max_results=25)", workflow)
        self.assertIn("artifacts/mob-x-discovery-probe.json", workflow)
        self.assertIn("name: mob-x-discovery-probe", workflow)
        self.assertIn("media_downloads", workflow)
        self.assertIn('"d1_reads": 0', workflow)
        self.assertIn('"d1_writes": 0', workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("--publish", workflow)
        self.assertNotIn("RubikaPublisher", workflow)


if __name__ == "__main__":
    unittest.main()
