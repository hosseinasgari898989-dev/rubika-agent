from __future__ import annotations

import unittest

from agent.brain.owner_notify import _rubika_format_payload, render_daily_digest


class OwnerDigestFormattingTests(unittest.TestCase):
    def test_rubka_parser_emits_metadata_for_supported_markdown_styles(self):
        payload = _rubika_format_payload("**bold** __italic__ ||spoiler||\n> quoted line")
        self.assertIn("text", payload)
        self.assertIn("metadata", payload)
        parts = payload["metadata"]["meta_data_parts"]
        kinds = {part.get("type") for part in parts}
        self.assertTrue({"Bold", "Italic", "Spoiler", "Quote"}.issubset(kinds))
        self.assertNotIn("**", payload["text"])
        self.assertNotIn("||", payload["text"])

    def test_daily_digest_has_formatted_header_and_dynamic_text_cannot_inject_styles(self):
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
        self.assertIn("**🧠 اعلامیهٔ روزانهٔ مغز Rubika-Agent**", body)
        self.assertNotIn("**not bold**", body)
        self.assertNotIn("||not spoiler||", body)
        payload = _rubika_format_payload(body)
        self.assertTrue(payload.get("metadata", {}).get("meta_data_parts"))


if __name__ == "__main__":
    unittest.main()
