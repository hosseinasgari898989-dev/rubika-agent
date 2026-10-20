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

    def test_unused_private_command_poller_is_removed_only(self):
        workflows = ROOT / ".github" / "workflows"
        self.assertFalse((workflows / "owner-console-poll.yml").exists())
        # Keep the independent automations untouched.
        for preserved in (
            "owner-daily-digest.yml",
            "owner-digest-refresh.yml",
            "daily-queue-processor.yml",
            "termux-docker-discovery.yml",
        ):
            with self.subTest(preserved=preserved):
                self.assertTrue((workflows / preserved).is_file())

    def test_edit_queue_uses_main_digest_and_four_success_cap(self):
        workflow = (ROOT / ".github" / "workflows" / "daily-queue-processor.yml").read_text(encoding="utf-8")
        worker = (ROOT / "agent" / "brain" / "queue_worker.py").read_text(encoding="utf-8")
        self.assertIn("ref: main", workflow)
        self.assertIn("--daily-limit 4", workflow)
        self.assertIn("DAILY_SUCCESS_LIMIT = 4", worker)
        self.assertIn("edit_message_text", (ROOT / "agent" / "brain" / "owner_notify.py").read_text(encoding="utf-8"))

    def test_manual_four_tiktok_dispatch_is_explicit_and_test_channel_only(self):
        workflow = (ROOT / ".github" / "workflows" / "daily-queue-processor.yml").read_text(encoding="utf-8")
        worker = (ROOT / "agent" / "brain" / "queue_worker.py").read_text(encoding="utf-8")
        self.assertIn("publish_four_tiktok_test", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("inputs.publish_four_tiktok_test == true", workflow)
        self.assertIn('if [[ "$GITHUB_EVENT_NAME" == "workflow_dispatch" ]]', workflow)
        self.assertIn('RUBIKA_CHANNEL_GUID:-}" != "c0EFohK0f3174377fb6fa422d3ccb230"', workflow)
        # The scheduled-run test-channel guard was removed on purpose (owner-approved).
        self.assertNotIn('RUBIKA_CHANNEL_GUID:-}" == "c0EFohK0f3174377fb6fa422d3ccb230"', workflow)
        self.assertIn("PLATFORMS = (\"tiktok\",)", worker)
        self.assertIn("--daily-limit 4", workflow)

    def test_edit_pipeline_does_not_apply_mob_birth_cutoff(self):
        intake = (ROOT / "agent" / "brain" / "queue_intake.py").read_text(encoding="utf-8")
        worker = (ROOT / "agent" / "brain" / "queue_worker.py").read_text(encoding="utf-8")
        discovery = (ROOT / "termux" / "daily_discovery.py").read_text(encoding="utf-8")
        self.assertNotIn("candidate_passes_birth_cutoff", intake)
        self.assertNotIn("candidate_passes_birth_cutoff", worker)
        self.assertIn("mob_candidates, stale_mob = filter_candidates_by_birth", discovery)
        self.assertNotIn("tiktok_candidates, stale_tiktok = filter_candidates_by_birth", discovery)

    def test_official_news_is_x_only_production_with_fixed_test_target_and_birth_cutoff(self):
        config = json.loads((ROOT / "config" / "mob_official_sources.json").read_text(encoding="utf-8"))
        policy = json.loads((ROOT / "config" / "agent_lifecycle.json").read_text(encoding="utf-8"))
        publishing = config["publishing"]
        self.assertTrue(publishing["enabled"])
        self.assertEqual(publishing["mode"], "production")
        self.assertTrue(publishing["production_enabled"])
        self.assertEqual(publishing["target_channel_guid"], "c0EFohK0f3174377fb6fa422d3ccb230")
        self.assertEqual(publishing["max_posts_per_run"], 1)
        self.assertEqual(policy["mode"], "production")
        self.assertEqual(policy["born_at"], "2026-10-20T00:00:00Z")

        sources = {
            row["id"] for row in config.get("official_sources", [])
            if row.get("enabled") is True
        }
        self.assertEqual(sources, {"mob_x"})

        production = (ROOT / ".github" / "workflows" / "mob-x-news-production.yml").read_text(encoding="utf-8")
        self.assertIn("RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014 == 'approved'", production)
        self.assertIn("--source-platform x", production)
        self.assertIn('RUBIKA_CHANNEL_GUID" != "c0EFohK0f3174377fb6fa422d3ccb230', production)
        self.assertNotIn("Production Mob publishing cannot target the known test channel.", production)

        trial = (ROOT / ".github" / "workflows" / "mob-official-trial.yml").read_text(encoding="utf-8")
        self.assertIn("ref: main", trial)
        self.assertIn("--publish-test-channel", trial)
        self.assertIn("RUBIKA_CHANNEL_GUID: c0EFohK0f3174377fb6fa422d3ccb230", trial)

        trial_code = (ROOT / "agent" / "brain" / "mob_official_trial.py").read_text(encoding="utf-8")
        self.assertIn('if kind not in {"image", "video"} or not media_url:', trial_code)
        self.assertIn('raise RuntimeError("original_media_unavailable")', trial_code)


if __name__ == "__main__":
    unittest.main()
