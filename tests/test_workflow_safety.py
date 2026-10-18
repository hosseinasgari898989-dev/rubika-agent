from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowSafetyTests(unittest.TestCase):
    def test_media_publishing_e2e_is_manual_and_fail_closed(self):
        workflow = (ROOT / ".github" / "workflows" / "brain-e2e-dispatch.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn('      - "artifacts/brain-e2e-request.json"', workflow)
        self.assertIn("RUBIKA_AGENT_E2E_PUBLISH_GATE_20261014 == 'approved'", workflow)
        self.assertIn("RUBIKA_TOKEN", workflow)
        # Merely changing the request file must not launch downloads or publication.
        self.assertNotIn("\n  push:\n", workflow)

    def test_official_news_publishing_is_locked_to_test_channel_only(self):
        config = json.loads((ROOT / "config" / "mob_official_sources.json").read_text(encoding="utf-8"))
        policy = json.loads((ROOT / "config" / "agent_lifecycle.json").read_text(encoding="utf-8"))
        publishing = config["publishing"]
        self.assertTrue(publishing["enabled"])
        self.assertEqual(publishing["mode"], "test_channel_only")
        self.assertEqual(publishing["target_channel_guid"], "c0EFohK0f3174377fb6fa422d3ccb230")
        self.assertEqual(publishing["max_posts_per_run"], 1)
        self.assertFalse(publishing["production_enabled"])
        self.assertEqual(policy["mode"], "test")
        workflow = (ROOT / ".github" / "workflows" / "mob-official-trial.yml").read_text(encoding="utf-8")
        self.assertIn("--publish-test-channel", workflow)
        self.assertIn("RUBIKA_CHANNEL_GUID: c0EFohK0f3174377fb6fa422d3ccb230", workflow)
        enabled = {
            row["id"] for row in config.get("official_sources", [])
            if row.get("enabled") is True
        }
        self.assertEqual(enabled, {"mob_x"})


    def test_video_e2e_trial_cannot_publish_on_push(self):
        workflow = (ROOT / ".github" / "workflows" / "video-e2e-trial.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("\\n  push:\\n", workflow)
        self.assertIn("confirm_test_channel_publish", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("if: ${{ inputs.confirm_test_channel_publish == true }}", workflow)
        self.assertIn('RUBIKA_CHANNEL_GUID: c0EFohK0f3174377fb6fa422d3ccb230', workflow)
        self.assertIn("ref: ${{ github.ref_name }}", workflow)

    def test_main_channel_activation_is_documented_without_enabling_production(self):
        runbook = (ROOT / "docs" / "MAIN_CHANNEL_ACTIVATION_RUNBOOK_FA.md").read_text(encoding="utf-8")
        lifecycle = json.loads((ROOT / "config" / "agent_lifecycle.json").read_text(encoding="utf-8"))
        self.assertIn("RUBIKA_AGENT_MAIN_PUBLISH_GATE_20261019", runbook)
        self.assertEqual(lifecycle["mode"], "test")
        self.assertIsNone(lifecycle["born_at"])
        config = json.loads((ROOT / "config" / "mob_official_sources.json").read_text(encoding="utf-8"))
        self.assertFalse(config["publishing"]["production_enabled"])


if __name__ == "__main__":
    unittest.main()
