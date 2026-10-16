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

    def test_edit_pipeline_does_not_apply_mob_birth_cutoff(self):
        intake = (ROOT / "agent" / "brain" / "queue_intake.py").read_text(encoding="utf-8")
        worker = (ROOT / "agent" / "brain" / "queue_worker.py").read_text(encoding="utf-8")
        discovery = (ROOT / "termux" / "daily_discovery.py").read_text(encoding="utf-8")
        self.assertNotIn("candidate_passes_birth_cutoff", intake)
        self.assertNotIn("candidate_passes_birth_cutoff", worker)
        self.assertIn("mob_candidates, stale_mob = filter_candidates_by_birth", discovery)
        self.assertNotIn("tiktok_candidates, stale_tiktok = filter_candidates_by_birth", discovery)

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


    def test_gemini_diagnosis_is_gated_advisory_only_and_d1_accounted(self):
        workflow = (ROOT / ".github" / "workflows" / "gemini-error-diagnosis.yml").read_text(encoding="utf-8")
        supervisor = (ROOT / "agent" / "brain" / "error_supervisor.py").read_text(encoding="utf-8")
        repair = (ROOT / ".github" / "workflows" / "groq-openrouter-code-repair.yml").read_text(encoding="utf-8")
        self.assertIn("RUBIKA_AGENT_AI_FREE_TIER_APPROVED", workflow)
        self.assertIn("RUBIKA_AGENT_GEMINI_ONLY_WORKER_READY", workflow)
        self.assertIn("status?provider=gemini", workflow)
        self.assertIn("--gemini-diagnosis", workflow)
        self.assertNotIn("multi_ai_code_repair", workflow)
        self.assertIn('provider="gemini"', supervisor)
        self.assertIn('role="diagnose"', supervisor)
        self.assertIn('"patch_ready": False', supervisor)
        self.assertIn('"code_changes_made": False', supervisor)
        self.assertNotIn("GROQ_API_KEY", repair)
        self.assertNotIn("OPENROUTER_API_KEY", repair)
        self.assertIn("No provider requests are sent", repair)

    def test_five_hour_mob_autopilot_is_test_channel_only_and_news_timestamp_scoped(self):
        workflow = (ROOT / ".github" / "workflows" / "mob-x-news-test-autopilot.yml").read_text(encoding="utf-8")
        trial = (ROOT / "agent" / "brain" / "mob_official_trial.py").read_text(encoding="utf-8")
        lifecycle = json.loads((ROOT / "config" / "agent_lifecycle.json").read_text(encoding="utf-8"))
        config = json.loads((ROOT / "config" / "mob_official_sources.json").read_text(encoding="utf-8"))
        self.assertIn('cron: "0 */5 * * *"', workflow)
        self.assertIn("RUBIKA_AGENT_MOB_X_TEST_AUTOPILOT_APPROVED", workflow)
        self.assertIn("RUBIKA_AGENT_AI_FREE_TIER_APPROVED", workflow)
        self.assertIn("RUBIKA_AGENT_GEMINI_ONLY_WORKER_READY", workflow)
        self.assertIn("RUBIKA_CHANNEL_GUID: c0EFohK0f3174377fb6fa422d3ccb230", workflow)
        self.assertIn("--source-platform x", workflow)
        self.assertIn("--published-within-hours 5", workflow)
        self.assertIn("seq 1 12", workflow)
        self.assertIn("publication_window_rejection", trial)
        self.assertIn("freshness_verified", trial)
        self.assertEqual(lifecycle["mode"], "test")
        self.assertEqual(config["publishing"]["mode"], "test_channel_only")
        self.assertFalse(config["publishing"]["production_enabled"])
        self.assertNotIn("termux.daily", workflow)

    def test_workers_ai_is_not_used_for_translation_or_error_diagnosis(self):
        worker = (ROOT / "src" / "index.js").read_text(encoding="utf-8")
        provider = (ROOT / "agent" / "brain" / "ai_provider_clients.py").read_text(encoding="utf-8")
        translation = (ROOT / "agent" / "brain" / "mob_translation.py").read_text(encoding="utf-8")
        self.assertEqual(worker.count("env.AI.run("), 1)
        self.assertIn("@cf/cloudflare/clef-omni", worker)
        self.assertIn('PROVIDERS = frozenset({"gemini"})', provider)
        self.assertIn('for provider in ("gemini",)', translation)
        self.assertNotIn("GoogleTranslator", translation)
        self.assertNotIn("GROQ_API_KEY", provider)
        self.assertNotIn("OPENROUTER_API_KEY", provider)

if __name__ == "__main__":
    unittest.main()
