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
        self.assertTrue({"mob_newsroom", "mob_youtube", "steam_news", "mob_x"}.issubset(enabled))


    def test_scheduled_discovery_uses_official_termux_mirror_fallback_and_live_health_route(self):
        workflow = (ROOT / ".github" / "workflows" / "termux-docker-discovery.yml").read_text(encoding="utf-8")
        # Avoid the retired supervisor route (removed from the deployed Worker).
        self.assertNotIn("/api/brain/supervise-error", workflow)
        # Check the real read-only health endpoint before spending time on discovery.
        self.assertIn('"$AGENT_API_BASE_URL/health"', workflow)
        self.assertIn('payload.get("database") != "connected"', workflow)
        # A rate-limited community mirror must not permanently block the five-hour run.
        self.assertIn("packages.termux.dev/apt/termux-main", workflow)
        self.assertIn("packages-cf.termux.dev/apt/termux-main", workflow)
        self.assertIn("Termux package index could not be refreshed from official mirrors.", workflow)

if __name__ == "__main__":
    unittest.main()
