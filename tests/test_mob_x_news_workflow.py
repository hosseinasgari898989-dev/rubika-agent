from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "mob-x-news-production.yml"


class MobXNewsWorkflowTests(unittest.TestCase):
    def test_x_cli_install_uses_the_two_independent_pinned_checksum_gates(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        lines = [line.strip() for line in workflow.splitlines()]
        checksum_lines = [
            line for line in lines
            if "sha256sum --check --status" in line
        ]
        self.assertEqual(len(checksum_lines), 2)
        self.assertIn(
            'printf \'%s  %s\\n\' "$expected_checksums_sha256" "$checksums"',
            checksum_lines[0],
        )
        self.assertIn(
            'printf \'%s  %s\\n\' "$published_checksum" "$archive"',
            checksum_lines[1],
        )
        self.assertIn('expected_archive_sha256="d722fd04f05c3785202846a1400caa9bcc0f8237947c0e35f6e8cf9efcbd1dd7"', workflow)
        self.assertIn('expected_checksums_sha256="ed391cfa2726d34e7de61ee93aca443731d10bca382da17f0b9683a8cac7c887"', workflow)

    def test_install_failure_keeps_public_fallback_and_does_not_change_schedule(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("x-cli setup unavailable; automatic Mob discovery will use FxTwitter and existing public fallbacks.", workflow)
        self.assertIn('echo "MOB_X_CLI_PATH=$binary" >> "$GITHUB_ENV"', workflow)
        self.assertIn('cron: "7 */5 * * *"', workflow)


if __name__ == "__main__":
    unittest.main()
