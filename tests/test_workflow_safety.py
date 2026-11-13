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

    def test_online_discovery_installs_ffmpeg_before_full_unit_suite(self):
        workflow = (ROOT / ".github" / "workflows" / "termux-docker-discovery.yml").read_text(encoding="utf-8")
        install_step = "      - name: Install FFmpeg for the complete repository test suite"
        test_step = "      - name: Run all repository unit tests"
        self.assertIn(install_step, workflow)
        self.assertLess(workflow.index(install_step), workflow.index(test_step))
        install_block = workflow.split(install_step, 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("sudo apt-get install --yes ffmpeg", install_block)
        self.assertIn("ffmpeg -hide_banner -filters", install_block)
        self.assertIn("libass", install_block)
        self.assertIn("ffprobe -version", install_block)

    def test_online_discovery_uses_main_as_single_source_of_truth(self):
        workflow = (ROOT / ".github" / "workflows" / "termux-docker-discovery.yml").read_text(encoding="utf-8")
        self.assertIn("ref: main", workflow)
        self.assertIn("push origin HEAD:refs/heads/main", workflow)
        self.assertNotIn("agent-brain-mega-v1", workflow)

    def test_online_discovery_keeps_git_auth_on_the_host_only(self):
        workflow = (ROOT / ".github" / "workflows" / "termux-docker-discovery.yml").read_text(encoding="utf-8")
        checkout = workflow.split("name: Check out the main discovery code", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("persist-credentials: false", checkout)
        self.assertNotIn("persist-credentials: true", workflow)

        push_step = workflow.split("name: Validate report and push only the metadata report to the main branch", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("GH_TOKEN: ${{ github.token }}", push_step)
        self.assertIn("base64 --wrap=0", push_step)
        self.assertIn('git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic ${auth_header}" fetch origin main', push_step)
        self.assertIn('git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic ${auth_header}" push origin HEAD:refs/heads/main', push_step)
        self.assertNotIn("git config --global", push_step)

        docker_step = workflow.split("name: Run real Termux Docker source tests and collect live metadata", 1)[1].split("\n      - name:", 1)[0]
        self.assertNotIn("GH_TOKEN", docker_step)
        self.assertIn('-v "$GITHUB_WORKSPACE:/workspace:ro"', docker_step)

    def test_owner_console_poller_schedule_is_disabled_and_manual_dispatch_preserved(self):
        workflows = ROOT / ".github" / "workflows"
        workflow = (workflows / "owner-console-poll.yml").read_text(encoding="utf-8")
        trigger = workflow.split("permissions:", 1)[0]
        self.assertNotIn("schedule:", trigger)
        self.assertNotIn('cron: "*/5 * * * *"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("timeout-minutes: 4", workflow)
        self.assertIn("permissions:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("python -m agent.brain.owner_console --once", workflow)
        for secret in ("RUBIKA_TOKEN", "RUBIKA_OWNER_CHAT_ID", "AGENT_API_TOKEN"):
            with self.subTest(secret=secret):
                self.assertIn(f"secrets.{secret}", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn("RUBIKA_OWNER_USER_ID", workflow)
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

    def test_edit_queue_has_no_send_count_cap_and_keeps_a_safe_time_budget(self):
        workflow = (ROOT / ".github" / "workflows" / "daily-queue-processor.yml").read_text(encoding="utf-8")
        worker = (ROOT / "agent" / "brain" / "queue_worker.py").read_text(encoding="utf-8")
        self.assertIn("ref: main", workflow)
        self.assertIn("--time-budget-seconds 7200", workflow)
        self.assertIn("--send-delay-seconds 3", workflow)
        self.assertIn("timeout-minutes: 180", workflow)
        self.assertNotIn("--daily-limit", workflow)
        self.assertNotIn("--max-attempts", workflow)
        self.assertNotIn("DAILY_SUCCESS_LIMIT", worker)
        self.assertNotIn("MAX_CANDIDATE_ATTEMPTS", worker)
        self.assertIn("edit_message_text", (ROOT / "agent" / "brain" / "owner_notify.py").read_text(encoding="utf-8"))

    def test_manual_and_automated_queue_paths_are_pinned_to_test_channel(self):
        workflow = (ROOT / ".github" / "workflows" / "daily-queue-processor.yml").read_text(encoding="utf-8")
        worker = (ROOT / "agent" / "brain" / "queue_worker.py").read_text(encoding="utf-8")
        guard = '0c939688dd5536447389134ba514e9353a6ef97214de3c4f848adb53e1cd4ab8'
        self.assertIn("publish_four_tiktok_test", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("inputs.publish_four_tiktok_test == true", workflow)
        self.assertIn("sha256sum", workflow)
        self.assertIn(guard, workflow)
        self.assertLess(workflow.index("actual_hash="), workflow.index('if [[ "$GITHUB_EVENT_NAME" == "workflow_dispatch" ]]'))
        self.assertIn('if [[ "$actual_hash" !=', workflow)
        self.assertIn("PLATFORMS = (\"tiktok\",)", worker)
        self.assertNotIn("--daily-limit", workflow)

    def test_discovery_sync_chains_into_one_serialized_queue_processor(self):
        workflows = ROOT / ".github" / "workflows"
        processor = (workflows / "daily-queue-processor.yml").read_text(encoding="utf-8")
        discovery = (workflows / "termux-docker-discovery.yml").read_text(encoding="utf-8")
        recovery = (workflows / "queue-intake.yml").read_text(encoding="utf-8")
        group = "rubika-agent-daily-queue-processor"
        self.assertIn(f"group: {group}", processor)
        self.assertIn("cancel-in-progress: false", processor)
        dispatch = discovery.split("name: Start the serialized TEST-only TikTok queue processor after successful sync", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("steps.queue_intake.outcome == 'success'", dispatch)
        self.assertLess(discovery.index("name: Start the serialized TEST-only TikTok queue processor after successful sync"), discovery.index("name: Record successful five-hour collection cycle"))
        self.assertIn("daily-queue-processor.yml", dispatch)
        self.assertIn('"publish_four_tiktok_test":"true"', dispatch)
        self.assertIn('"publish_four_tiktok_test":"true"', recovery)
        # Existing discovery throttles remain unchanged.
        self.assertIn('MINIMUM_INTERVAL = timedelta(hours=5)', (ROOT / "termux" / "collection_gate.py").read_text(encoding="utf-8"))
        self.assertIn("max_per_profile=8", (ROOT / "termux" / "daily_discovery.py").read_text(encoding="utf-8"))
        self.assertIn("--max-new 4", discovery)

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
        self.assertEqual(publishing["target_channel_guid_sha256"], "0c939688dd5536447389134ba514e9353a6ef97214de3c4f848adb53e1cd4ab8")
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
        self.assertIn("sha256sum", production)
        self.assertIn("0c939688dd5536447389134ba514e9353a6ef97214de3c4f848adb53e1cd4ab8", production)
        self.assertNotIn("Production Mob publishing cannot target the known test channel.", production)

        trial = (ROOT / ".github" / "workflows" / "mob-official-trial.yml").read_text(encoding="utf-8")
        self.assertIn("ref: main", trial)
        self.assertIn("--publish-test-channel", trial)
        self.assertIn("secrets.RUBIKA_CHANNEL_GUID", trial)
        self.assertIn("0c939688dd5536447389134ba514e9353a6ef97214de3c4f848adb53e1cd4ab8", trial)

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

    def test_mega_retention_workflow_is_dry_run_only(self):
        workflow = (ROOT / ".github" / "workflows" / "mega-retention-cleanup.yml").read_text(encoding="utf-8")
        trigger = workflow.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        self.assertIn("default: true", trigger)
        self.assertNotIn("\n  push:\n", trigger)
        self.assertNotIn("schedule:", trigger)
        self.assertIn("inputs.dry_run == true", workflow)
        self.assertIn("Only dry_run=true is supported", workflow)
        self.assertIn("python -m agent.brain.storage_cleanup", workflow)
        self.assertIn("--min-age-hours 24", workflow)
        self.assertIn("--max-delete 25", workflow)
        self.assertNotIn("--apply", workflow)
        self.assertNotIn("mega-rm", workflow)

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
        self.assertIn("secrets.RUBIKA_CHANNEL_GUID", workflow)
        self.assertIn("ref: main", workflow)

    def test_batch_e2e_requires_the_repo_gate_and_explicit_manual_confirmation(self):
        workflow = (ROOT / ".github" / "workflows" / "brain-e2e-dispatch.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("\n  push:\n", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("inputs.confirm_test_channel_publish == true", workflow)
        self.assertIn("vars.RUBIKA_AGENT_E2E_PUBLISH_GATE_20261014 == 'approved'", workflow)
        self.assertIn("secrets.RUBIKA_CHANNEL_GUID", workflow)
        self.assertIn("ref: main", workflow)
        self.assertIn("cloud discovery report", workflow)



    def test_youtube_park_workflow_is_manual_fail_closed_and_uses_only_existing_token(self):
        workflow = (ROOT / ".github" / "workflows" / "park-youtube-queue.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("\\n  schedule:", workflow)
        self.assertNotIn("\\n  push:", workflow)
        self.assertIn("default: true", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("inputs.dry_run", workflow)
        self.assertIn("inputs.confirm", workflow)
        self.assertIn("secrets.AGENT_API_TOKEN", workflow)
        self.assertNotIn("secrets.MEGA", workflow)
        self.assertIn("/api/brain/automation-queue/park-youtube", workflow)
        self.assertIn("dry_run=false", workflow)
        self.assertIn("confirm=true", workflow)
        self.assertIn("changed_count", workflow)
        self.assertNotIn("workflow_dispatch", workflow.split("jobs:", 1)[1])


    def test_mob_x_terminal_media_cleanup_is_existing_schedule_and_unique_path_only(self):
        workflow = (ROOT / ".github" / "workflows" / "mob-x-link-processor.yml").read_text(encoding="utf-8")
        self.assertIn("cleanup-terminal-unique-media:", workflow)
        self.assertIn("github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn("secrets.AGENT_API_TOKEN", workflow)
        self.assertIn("secrets.MEGA_EMAIL", workflow)
        self.assertIn("secrets.MEGA_PASSWORD", workflow)
        self.assertIn("python -m agent.brain.mob_x_media_cleanup", workflow)
        cleanup = (ROOT / "agent" / "brain" / "mob_x_media_cleanup.py").read_text(encoding="utf-8")
        self.assertIn("UNIQUE_OFFICIAL_KEY", cleanup)
        self.assertIn('result.get("delivery_status") != "sent"', cleanup)
        self.assertIn('TERMINAL_STATUSES = ("sent",)', cleanup)
        self.assertNotIn('"POST"', cleanup)
        self.assertNotIn("status=uncertain", cleanup)

if __name__ == "__main__":
    unittest.main()

