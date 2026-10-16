import unittest

from agent.brain.edit_caption import EDIT_CAPTION
from agent.brain.e2e_batch_from_report import _round_robin_candidates
from agent.brain.e2e_from_report import _stage_failure_summary
from agent.brain.e2e_from_report import _blocking_ledger_ids, _is_reusable_staged_item
from agent.brain.models import Candidate


def _candidate(platform: str, external_id: str, title: str) -> Candidate:
    return Candidate(
        platform=platform,
        canonical_url=f"https://example.test/{external_id}",
        external_id=external_id,
        title=title,
        caption=title,
        source_key="creator",
        media_type="video",
        source_tier="unknown",
    )


class StageFailureDiagnosticsTests(unittest.TestCase):
    def test_returns_first_actionable_reason_without_provider_body(self):
        result = _stage_failure_summary({
            "status": "partial",
            "staged_count": 0,
            "skipped": [
                {"item": "tiktok:123", "reason": "music_probability_ambiguous"},
                {"item": "tiktok:124", "reason": "permission_gate_failed"},
            ],
            "cloud_sync_errors": [],
        })
        self.assertEqual(result["reason"], "music_probability_ambiguous")
        self.assertEqual(len(result["failure_details"]), 2)
        self.assertEqual(result["failure_details"][1]["reason"], "permission_gate_failed")

    def test_prioritizes_d1_sync_failure_without_leaking_error_text(self):
        result = _stage_failure_summary({
            "status": "partial",
            "staged_count": 1,
            "skipped": [],
            "cloud_sync_errors": ["tiktok:123:CloudStateError private-token-should-not-appear"],
        })
        self.assertEqual(result["reason"], "cloud_sync_failed")
        self.assertTrue(result["cloud_sync_errors"])
        self.assertNotIn("private-token-should-not-appear", str(result))


class DeliveryAwareDeduplicationTests(unittest.TestCase):
    def test_only_delivery_blocking_ledger_entries_exclude_queue_candidates(self):
        ids = _blocking_ledger_ids({
            "tiktok:ready": {
                "platform": "tiktok", "external_id": "ready", "status": "ready",
            },
            "tiktok:failed": {
                "platform": "tiktok", "external_id": "failed", "status": "failed",
            },
            "tiktok:sent": {
                "platform": "tiktok", "external_id": "sent", "status": "sent",
            },
        })
        self.assertNotIn("tiktok:ready", ids)
        self.assertNotIn("tiktok:failed", ids)
        self.assertIn("tiktok:sent", ids)
        self.assertIn("sent", ids)

    def test_reuses_only_validated_ready_media_with_successful_cloud_sync(self):
        candidate = _candidate("tiktok", "123", "Poppy Playtime edit")
        entry = {
            "status": "ready",
            "platform": "tiktok",
            "external_id": "123",
            "permission_confirmed": True,
            "cloud_sync": "ok",
            "media_type": "video",
            "storage_key": "/Rubika-Agent/staging/tiktok_123-preserved.mp4",
            "sha256": "a" * 64,
            "cloud_payload": {
                "media_type": "video",
                "media_sha256": "a" * 64,
                "validation": {"ok": True, "mime_type": "video/mp4"},
            },
        }
        self.assertTrue(_is_reusable_staged_item(candidate, entry))
        for patch in (
            {"status": "failed"},
            {"permission_confirmed": False},
            {"cloud_sync": "pending"},
            {"media_type": "unknown"},
            {"storage_key": "/Rubika-Agent/../outside.mp4"},
            {"cloud_payload": {"media_type": "video", "media_sha256": "b" * 64, "validation": {"ok": True}}},
        ):
            invalid = dict(entry)
            invalid.update(patch)
            self.assertFalse(_is_reusable_staged_item(candidate, invalid), patch)


class BatchOrderTests(unittest.TestCase):
    def test_batch_round_robins_tiktok_and_youtube_and_removes_duplicate_ids(self):
        candidates = [
            _candidate("tiktok", "tt1", "Poppy Playtime edit one"),
            _candidate("tiktok", "tt2", "Huggy Wuggy edit two"),
            _candidate("youtube", "yt1", "CatNap Poppy Playtime edit three"),
            _candidate("youtube", "yt2", "Smiling Critters edit four"),
            _candidate("tiktok", "tt1", "Poppy Playtime edit duplicate"),
            _candidate("youtube", "yt3", "Cooking video unrelated"),
        ]

        ordered = _round_robin_candidates(candidates, platforms=("tiktok", "youtube"))

        self.assertEqual(
            [(item.platform, item.external_id) for item in ordered],
            [
                ("tiktok", "tt1"),
                ("youtube", "yt1"),
                ("tiktok", "tt2"),
                ("youtube", "yt2"),
            ],
        )

    def test_edit_caption_has_channel_label_and_standard_branding(self):
        self.assertTrue(EDIT_CAPTION.startswith("**𝔓𝔒𝔓𝔜 𝔓𝔏𝔄𝔜𝔗ℑ𝔐𝔈**"))
        self.assertIn("CHANEL» @poppy_play_time_HV", EDIT_CAPTION)
        self.assertIn("ˡⁱᵏᵉ", EDIT_CAPTION)


if __name__ == "__main__":
    unittest.main()
