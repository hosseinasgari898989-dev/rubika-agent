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

    def test_online_discovery_uses_main_as_single_source_of_truth(self):
        workflow = (ROOT / ".github" / "workflows" / "termux-docker-discovery.yml").read_text(encoding="utf-8")
        self.assertIn("ref: main", workflow)
        self.assertIn("git push origin HEAD:refs/heads/main", workflow)
        self.assertNotIn("agent-brain-mega-v1", workflow)

    def test_owner_console_poller_is_five_minute_serial_and_read_only(self):
        workflows = ROOT / ".github" / "workflows"
        workflow = (workflows / "owner-console-poll.yml").read_text(encoding="utf-8")
        self.assertIn("schedule:", workflow)
        self.assertIn('cron: "*/5 * * * *"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("timeout-minutes: 4", workflow)
        self.assertIn("permissions:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("python -m agent.brain.owner_console --once", workflow)
        for secret in ("RUBIKA_TOKEN", "RUBIKA_OWNER_CHAT_ID", "RUBIKA_OWNER_USER_ID", "AGENT_API_TOKEN"):
            with self.subTest(secret=secret):
                self.assertIn(f"secrets.{secret}", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn("RUBIKA_CHANNEL_GUID", workflow)
        self.assertNotIn("send_media", workflow)
        # Keep independent automations intact.
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

    def test_retired_provider_and_dispatch_placeholders_stay_removed(self):
        workflows = ROOT / ".github" / "workflows"
        for retired in (
            "supervisor-inference-probe.yml",
            "dispatch-probe.yml",
        ):
            with self.subTest(retired=retired):
                self.assertFalse((workflows / retired).exists())

    def test_legacy_phone_termux_entrypoints_are_removed(self):
        self.assertFalse((ROOT / "termux" / "bootstrap.sh").exists())
        self.assertFalse((ROOT / "termux" / "daily.sh").exists())
        self.assertFalse((ROOT / "docs" / "TERMUX_DAILY_DISCOVERY.md").exists())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cloud_doc = (ROOT / "docs" / "CLOUD_DISCOVERY.md").read_text(encoding="utf-8")
        self.assertIn("docs/CLOUD_DISCOVERY.md", readme)
        self.assertIn("Termux Docker", cloud_doc)
        self.assertIn("GitHub Actions", cloud_doc)
        self.assertNotIn("bash termux/daily.sh", readme)
        self.assertNotIn("bash termux/bootstrap.sh", readme)

    def test_video_e2e_trial_is_manual_main_only_and_test_channel_locked(self):
        workflow = (ROOT / ".github" / "workflows" / "video-e2e-trial.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("\n  push:\n", workflow)
        self.assertIn("confirm_test_channel_publish", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("if: ${{ inputs.confirm_test_channel_publish == true }}", workflow)
        self.assertIn("RUBIKA_CHANNEL_GUID: c0EFohK0f3174377fb6fa422d3ccb230", workflow)
        self.assertIn("ref: main", workflow)

    def test_batch_e2e_requires_the_repo_gate_and_explicit_manual_confirmation(self):
        workflow = (ROOT / ".github" / "workflows" / "brain-e2e-dispatch.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("\n  push:\n", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("inputs.confirm_test_channel_publish == true", workflow)
        self.assertIn("vars.RUBIKA_AGENT_E2E_PUBLISH_GATE_20261014 == 'approved'", workflow)
        self.assertIn("RUBIKA_CHANNEL_GUID: c0EFohK0f3174377fb6fa422d3ccb230", workflow)
        self.assertIn("ref: main", workflow)
        self.assertIn("cloud discovery report", workflow)


if __name__ == "__main__":
    unittest.main()
