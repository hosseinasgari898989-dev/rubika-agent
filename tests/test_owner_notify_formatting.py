from __future__ import annotations

import unittest

from agent.brain.owner_notify import render_daily_digest


class OwnerDigestFormattingTests(unittest.TestCase):
    def test_daily_digest_uses_plain_text_and_dynamic_text_cannot_inject_styles(self):
        state = {
            "window_started_at": "2026-10-16T07:00:00Z",
            "window_ends_at": "2026-10-17T07:00:00Z",
            "counts": {"success": 2, "ai_success": 1, "error": 1, "ai_error": 0, "info": 1},
            "events": [{
                "category": "success",
                "occurred_at": "2026-10-16T08:00:00Z",
                "source": "queue_worker",
                "title": "Edited **not bold** ||not spoiler||",
                "detail": "A video was sent",
            }],
            "total_event_count": 1,
        }
        body = render_daily_digest(state)
        self.assertIn("🧠 اعلامیهٔ روزانهٔ مغز Rubika-Agent", body)
        self.assertNotIn("**🧠 اعلامیهٔ روزانهٔ مغز Rubika-Agent**", body)
        self.assertNotIn("**", body)
        self.assertNotIn("__", body)
        self.assertNotIn("||", body)
        self.assertIn("* *not bold* *", body)
        self.assertIn("| |not spoiler| |", body)


if __name__ == "__main__":
    unittest.main()
