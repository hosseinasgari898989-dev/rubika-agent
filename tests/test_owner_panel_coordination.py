from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OWNER_PANEL = (ROOT / "src" / "owner_panel.js").read_text(encoding="utf-8")


class OwnerPanelCoordinationTests(unittest.TestCase):
    def test_rubika_api_calls_have_a_twelve_second_timeout(self):
        self.assertIn("AbortSignal.timeout(12_000)", OWNER_PANEL)

    def test_panel_text_and_keypad_edits_are_concurrent(self):
        self.assertIn("Promise.allSettled", OWNER_PANEL)
        self.assertIn("editMessageKeypad", OWNER_PANEL)
        self.assertIn("editMessageText", OWNER_PANEL)

    def test_stale_panel_events_are_guarded_and_counted(self):
        self.assertIn('Symbol("panel_seq")', OWNER_PANEL)
        self.assertIn("assertPanelEventCurrent", OWNER_PANEL)
        self.assertIn('"panel_stale_skip"', OWNER_PANEL)
        self.assertIn("panel_seq", OWNER_PANEL)

    def test_report_fetches_components_in_parallel_without_progress_edits(self):
        self.assertIn("Promise.all([api(ui, \"/api/status\"), api(ui, \"/api/brain/owner-console/queue-summary\"), aiBlock(ui)])", OWNER_PANEL)
        self.assertNotIn('parts.push("⏳ ۱ از ۳', OWNER_PANEL)
        self.assertNotIn('parts.push("⏳ ۲ از ۳', OWNER_PANEL)
        self.assertNotIn('parts.push("⏳ ۳ از ۳', OWNER_PANEL)

    def test_fresh_text_commands_refresh_screen_panels(self):
        self.assertIn('if (fresh && ["p_errors", "p_runs", "p_links", "p_digest"].includes(id)) return sendFreshPanel(ui, s.text, keypad, previousMid);', OWNER_PANEL)

    def test_second_publish_click_explains_existing_approval(self):
        self.assertIn('row.status === "approved"', OWNER_PANEL)
        self.assertIn("تأیید قبلاً ثبت شده", OWNER_PANEL)


if __name__ == "__main__":
    unittest.main()
