from __future__ import annotations

import unittest

from agent.brain.models import Candidate, InspectionResult
from agent.brain.policy import evaluate_edit_candidate
from agent.source_policy import (
    delivery_enabled,
    known_tiktok_profiles,
    load_source_policy_config,
    resolve_source_policy,
)


class TikTokSourcePolicyTests(unittest.TestCase):
    def candidate(self, handle: str, title: str = "Unrelated video") -> Candidate:
        return Candidate(
            platform="tiktok",
            canonical_url=f"https://www.tiktok.com/@{handle}/video/7616247520657771796",
            external_id="7616247520657771796",
            title=title,
            caption="",
            source_key=handle,
            media_type="video",
            source_policy=resolve_source_policy("tiktok", handle),
        )

    @staticmethod
    def inspection(width: int = 1920, height: int = 1080) -> InspectionResult:
        return InspectionResult(
            ok=True,
            media_type="video",
            sha256="a" * 64,
            size_bytes=1_000_000,
            mime_type="video/mp4",
            width=width,
            height=height,
            duration_seconds=14.0,
            has_audio=True,
            video_codec="h264",
            audio_codec="aac",
        )

    @staticmethod
    def checks(**overrides):
        result = {
            "has_music": True,
            "is_poppy_playtime": True,
            "is_edit": True,
        }
        result.update(overrides)
        return result

    def test_six_owner_selected_profiles_are_configured(self):
        config = load_source_policy_config()
        self.assertEqual(
            known_tiktok_profiles(),
            ("meloaep_", "smyxae", "menezxl9", "rickkzn_", "saylex.aep812", "poppytv_ae"),
        )
        self.assertEqual(
            [row["handle"] for row in config["sources"]],
            list(known_tiktok_profiles()),
        )

    def test_first_two_and_third_owner_sources_are_square(self):
        for handle in ("meloaep_", "smyxae", "menezxl9"):
            with self.subTest(handle=handle):
                policy = resolve_source_policy("tiktok", handle)
                self.assertEqual(policy["output_format"], "square_1080x1080")
                self.assertTrue(policy["require_visual_poppy_check"])
                self.assertTrue(policy["require_visual_edit_check"])

    def test_three_sources_preserve_source_aspect_ratio(self):
        for handle in ("rickkzn_", "saylex.aep812", "poppytv_ae"):
            with self.subTest(handle=handle):
                policy = resolve_source_policy("tiktok", handle)
                self.assertEqual(policy["output_format"], "preserve_source")

    def test_only_menezxl9_requires_ai_edit_quality_check_among_known_sources(self):
        for handle in known_tiktok_profiles():
            with self.subTest(handle=handle):
                self.assertEqual(
                    resolve_source_policy("tiktok", handle)["require_ai_quality_check"],
                    handle == "menezxl9",
                )

    def test_unknown_sources_require_full_visual_quality_checks_and_square_output(self):
        policy = resolve_source_policy("tiktok", "unlisted_creator")
        self.assertFalse(policy["known_source"])
        self.assertEqual(policy["trust_tier"], "unknown")
        self.assertEqual(policy["output_format"], "square_1080x1080")
        self.assertTrue(policy["require_visual_poppy_check"])
        self.assertTrue(policy["require_visual_edit_check"])
        self.assertTrue(policy["require_ai_quality_check"])

    def test_selected_source_can_pass_without_metadata_claiming_poppy(self):
        candidate = self.candidate("meloaep_")
        result = evaluate_edit_candidate(
            candidate, self.inspection(),
            moderation=self.checks(),
        )
        self.assertTrue(result.eligible, result.reasons)
        self.assertEqual(result.checks["output_format"], "square_1080x1080")

    def test_known_source_is_rejected_if_visual_content_is_not_poppy(self):
        result = evaluate_edit_candidate(
            self.candidate("smyxae"), self.inspection(),
            moderation=self.checks(is_poppy_playtime=False),
        )
        self.assertIn("visual_content_not_poppy_playtime", result.reasons)

    def test_known_source_is_rejected_if_video_is_not_an_edit(self):
        result = evaluate_edit_candidate(
            self.candidate("poppytv_ae"), self.inspection(),
            moderation=self.checks(is_edit=False),
        )
        self.assertIn("visual_content_not_an_edit", result.reasons)

    def test_menezxl9_requires_and_checks_edit_quality(self):
        candidate = self.candidate("menezxl9")
        missing = evaluate_edit_candidate(candidate, self.inspection(), moderation=self.checks())
        self.assertEqual(missing.decision, "needs_review")
        self.assertIn("high_quality_edit", missing.checks["moderation_fields_missing"])

        low_quality = evaluate_edit_candidate(
            candidate, self.inspection(),
            moderation=self.checks(high_quality_edit=False),
        )
        self.assertIn("visual_edit_quality_below_policy", low_quality.reasons)

        high_quality = evaluate_edit_candidate(
            candidate, self.inspection(),
            moderation=self.checks(high_quality_edit=True),
        )
        self.assertTrue(high_quality.eligible, high_quality.reasons)

    def test_unknown_creator_requires_high_quality_answer(self):
        candidate = self.candidate("unlisted_creator", title="Poppy Playtime edit")
        result = evaluate_edit_candidate(
            candidate, self.inspection(), moderation=self.checks(),
        )
        self.assertEqual(result.decision, "needs_review")
        self.assertIn("high_quality_edit", result.checks["moderation_fields_missing"])

    def test_preserve_source_policy_does_not_add_square_conversion_warning(self):
        result = evaluate_edit_candidate(
            self.candidate("rickkzn_"),
            self.inspection(width=1080, height=1920),
            moderation=self.checks(),
        )
        self.assertTrue(result.eligible, result.reasons)
        self.assertNotIn("needs_square_1080x1080_normalization", result.warnings)
        self.assertEqual(result.checks["output_format"], "preserve_source")

    def test_youtube_delivery_remains_paused_while_discovery_policy_stays_separate(self):
        self.assertFalse(delivery_enabled("youtube"))
        self.assertFalse(delivery_enabled("tiktok"))


if __name__ == "__main__":
    unittest.main()
