from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "x-cli-probe.yml"


class XCliReadOnlyProbeWorkflowTests(unittest.TestCase):
    def test_workflow_is_manual_only_and_read_only(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        trigger = workflow.split("permissions:", 1)[0]
        self.assertRegex(trigger, r"(?m)^on:\s*\n\s+workflow_dispatch:\s*$")
        for forbidden in ("  schedule:", "  push:", "  pull_request:", "  workflow_run:"):
            self.assertNotIn(forbidden, trigger)
        permissions = workflow.split("permissions:", 1)[1].split("jobs:", 1)[0]
        self.assertRegex(permissions, r"(?m)^\s+contents:\s*read\s*$")
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("GITHUB_TOKEN", workflow)
        self.assertNotIn("RUBIKA_", workflow)
        self.assertNotIn("AGENT_API_", workflow)
        self.assertNotIn("CloudState", workflow)
        self.assertNotIn("D1", workflow)
        self.assertNotIn("Mega", workflow)
        self.assertNotIn("upload-artifact", workflow)
        self.assertNotIn("actions/checkout", workflow)

    def test_network_is_limited_to_release_download_and_x_cli_commands(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        curl_lines = [line for line in workflow.splitlines() if re.search(r"\bcurl\b", line)]
        self.assertEqual(len(curl_lines), 2)
        self.assertIn('base="https://github.com/tamnd/x-cli/releases/download/v$version"', workflow)
        self.assertTrue(all('"$base/' in line for line in curl_lines))
        self.assertIn("https://x.com/mobgamesstudios/status/1731735864110424148", workflow)
        self.assertNotIn("requests.", workflow)
        self.assertNotIn("wget ", workflow)
        self.assertNotIn("http://", workflow)

    def test_pinned_install_is_verified_fail_closed(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('version="0.5.0"', workflow)
        self.assertIn("d722fd04f05c3785202846a1400caa9bcc0f8237947c0e35f6e8cf9efcbd1dd7", workflow)
        self.assertIn("ed391cfa2726d34e7de61ee93aca443731d10bca382da17f0b9683a8cac7c887", workflow)
        self.assertIn("sha256sum --check --status", workflow)
        self.assertIn("fail_closed", workflow)
        self.assertIn("exit 1", workflow)
        self.assertNotIn("fallback remains enabled", workflow)

    def test_probe_flags_and_all_five_read_only_cases_are_present(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("env -i", workflow)
        self.assertIn("XDG_CONFIG_HOME", workflow)
        self.assertIn("XDG_DATA_HOME", workflow)
        self.assertIn("XDG_CACHE_HOME", workflow)
        self.assertIn("timeout 20s", workflow)
        self.assertIn("--retries=0", workflow)
        self.assertIn("--timeout=15s", workflow)
        self.assertIn("--no-cache", workflow)
        self.assertIn("--guest --tier=guest tweet https://x.com/mobgamesstudios/status/1731735864110424148", workflow)
        self.assertIn("timeline mobgamesstudios --guest -n 10 -o jsonl", workflow)
        self.assertIn("--tier=0 timeline mobgamesstudios -n 10 -o jsonl", workflow)
        self.assertIn("timeline mobgamesstudios --guest -n 40 -o jsonl", workflow)
        self.assertIn("run_probe D", workflow)
        self.assertIn("run_probe E", workflow)

    def test_only_safe_derived_fields_are_written_to_summary(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("GITHUB_STEP_SUMMARY", workflow)
        self.assertIn('len(str(row.get("text") or ""))', workflow)
        self.assertIn("media_stats", workflow)
        self.assertIn("rate-limit sign", workflow)
        self.assertIn("older_than_2026-11-05T00:00:00Z", workflow)
        self.assertIn("not_official_author", workflow)
        self.assertIn("retweeted_status_id", workflow)
        self.assertIn("quoted_status_id", workflow)
        self.assertIn('"quoted", "quoted_tweet"', workflow)
        self.assertIn("pinned_tweet", workflow)
        self.assertNotIn("print(raw", workflow)
        self.assertNotIn("print(stderr", workflow)
        self.assertNotIn("upload-artifact", workflow)
        self.assertNotIn('cat "$probe_dir/stdout"', workflow)
        self.assertNotIn('cat "$probe_dir/stderr"', workflow)


if __name__ == "__main__":
    unittest.main()
