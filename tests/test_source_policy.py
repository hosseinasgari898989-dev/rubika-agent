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
    def candidate(
        self,
        handle: str,
        title: str = "Poppy Playtime fan edit",
        caption: str = "#PoppyPlaytime #edit",
    ) -> Candidate:
        return Candidate(
            platform="tiktok",
            canonical_url=f"https://www.tiktok.com/@{handle}/video/7616247520657771796",
            external_id="7616247520657771796",
            title=title,
            caption=caption,
            source_key=handle,
            media_type="video",
            source_policy=resolve_source_policy("tiktok", handle),
        )

    @staticmethod
    def inspection(width: int = 1920, height: int = 1080, size_bytes: int = 1_000_000, codec: str = "h264") -> InspectionResult:
        return InspectionResult(
            ok=True,
            media_type="video",
            sha256="a" * 64,
            size_bytes=size_bytes,
            mime_type="video/mp4",
            width=width,
            height=height,
            duration_seconds=14.0,
            has_audio=True,
            video_codec=codec,
            audio_codec="aac",
        )

    def test_six_owner_selected_profiles_are_configured(self):
        config = load_source_policy_config()
        self.assertEqual(
            known_tiktok_profiles(),
            ("meloaep_", "smyxae", "menezxl9", "rickkzn_", "saylex.aep812", "poppytv_ae"),
        )
        self.assertEqual([row["handle"] for row in config["sources"]], list(known_tiktok_profiles()))

    def test_known_sources_keep_their_output_formats_with_relaxed_quality_filters(self):
        config = load_source_policy_config()
        sources = {row["handle"]: row for row in config["sources"]}
        for handle in ("meloaep_", "smyxae", "menezxl9"):
            with self.subTest(handle=handle):
                policy = resolve_source_policy("tiktok", handle)
                self.assertEqual(policy["output_format"], "square_1080x1080")
                self.assertTrue(policy["require_poppy_relevance_check"])
                self.assertTrue(policy["require_edit_signal_check"])
        for handle in ("rickkzn_", "saylex.aep812", "poppytv_ae"):
            with self.subTest(handle=handle):
                self.assertEqual(resolve_source_policy("tiktok", handle)["output_format"], "preserve_source")
        self.assertTrue(all(row["require_brain_quality_check"] is False for row in sources.values()))
        self.assertTrue(all(row["minimum_source_short_side"] == 480 for row in sources.values()))

    def test_unknown_sources_use_strict_brain_policy(self):
        policy = resolve_source_policy("tiktok", "unlisted_creator")
        self.assertFalse(policy["known_source"])
        self.assertEqual(policy["trust_tier"], "unknown")
        self.assertEqual(policy["output_format"], "square_1080x1080")
        self.assertEqual(policy["minimum_source_short_side"], 720)
        self.assertTrue(policy["require_poppy_relevance_check"])
        self.assertTrue(policy["require_edit_signal_check"])
        self.assertTrue(policy["require_brain_quality_check"])

    def test_known_source_needs_only_ai_music_plus_its_configured_brain_checks(self):
        candidate = self.candidate("meloaep_")
        result = evaluate_edit_candidate(
            candidate, self.inspection(), moderation={"has_music": True},
        )
        self.assertTrue(result.eligible, result.reasons)
        self.assertIs(result.checks["has_music"], True)
        self.assertIs(result.checks["is_poppy_playtime"], True)
        self.assertIs(result.checks["is_edit"], True)
        self.assertNotIn("high_quality_edit", result.checks)
        self.assertEqual(result.checks["topic_check_method"], "agent_brain_public_metadata")
        self.assertEqual(result.checks["edit_check_method"], "agent_brain_edit_signal_rules")

    def test_known_creator_accepts_an_edit_mention_without_a_poppy_keyword(self):
        candidate = self.candidate("smyxae", title="new clip", caption="@edit")
        result = evaluate_edit_candidate(
            candidate, self.inspection(), moderation={"has_music": True},
        )
        self.assertTrue(result.eligible, result.reasons)
        self.assertIs(result.checks["is_poppy_playtime"], True)
        self.assertIs(result.checks["is_edit"], True)

    def test_known_creator_accepts_a_poppy_tag_without_an_explicit_edit_marker(self):
        candidate = self.candidate("smyxae", title="new clip", caption="@poppy_playtime")
        result = evaluate_edit_candidate(
            candidate, self.inspection(), moderation={"has_music": True},
        )
        self.assertTrue(result.eligible, result.reasons)
        self.assertIs(result.checks["is_poppy_playtime"], True)
        self.assertIs(result.checks["is_edit"], True)

    def test_unknown_creator_accepts_compound_tags_and_playtime_mentions_but_keeps_quality_gate(self):
        candidate = self.candidate(
            "unlisted_creator",
            title="fresh clip",
            caption="@playtime #poppy_playtime_edits",
        )
        result = evaluate_edit_candidate(
            candidate, self.inspection(), moderation={"has_music": True},
        )
        self.assertTrue(result.eligible, result.reasons)
        self.assertIs(result.checks["is_poppy_playtime"], True)
        self.assertIs(result.checks["is_edit"], True)
        self.assertIs(result.checks["high_quality_edit"], True)

    def test_known_creator_username_does_not_make_unrelated_upload_relevant(self):
        result = evaluate_edit_candidate(
            self.candidate("smyxae", title="Unrelated gameplay", caption="#gaming"),
            self.inspection(),
            moderation={"has_music": True},
        )
        self.assertIn("poppy_relevance_not_found_by_brain", result.reasons)
        self.assertIn("edit_signal_not_found_by_brain", result.reasons)
        self.assertIs(result.checks["is_poppy_playtime"], False)
        self.assertIs(result.checks["is_edit"], False)

    def test_missing_music_is_the_only_missing_ai_moderation_field(self):
        result = evaluate_edit_candidate(
            self.candidate("meloaep_"),
            self.inspection(),
            moderation={
                "music_probability": 0.55,
                "music_decision_reason": "music_probability_ambiguous",
            },
        )
        self.assertEqual(result.decision, "needs_review")
        self.assertEqual(result.checks["moderation_fields_missing"], ["has_music"])
        self.assertEqual(result.checks["music_probability"], 0.55)
        self.assertEqual(result.checks["music_decision_reason"], "music_probability_ambiguous")

    def test_known_source_does_not_apply_the_extra_brain_quality_gate(self):
        candidate = self.candidate("menezxl9")
        result = evaluate_edit_candidate(
            candidate, self.inspection(), moderation={"has_music": True},
        )
        self.assertTrue(result.eligible, result.reasons)
        self.assertNotIn("high_quality_edit", result.checks)
        self.assertEqual(result.checks["quality_check_method"], "not_required_by_source_policy")

    def test_unknown_creator_must_pass_topic_edit_music_and_technical_gates(self):
        candidate = self.candidate("unlisted_creator")
        result = evaluate_edit_candidate(
            candidate, self.inspection(), moderation={"has_music": True},
        )
        self.assertTrue(result.eligible, result.reasons)
        self.assertIs(result.checks["is_poppy_playtime"], True)
        self.assertIs(result.checks["is_edit"], True)
        self.assertIs(result.checks["high_quality_edit"], True)
        self.assertEqual(result.checks["policy_track"], "unknown_creator_full_review")

    def test_unknown_creator_cannot_pass_quality_gate_at_low_resolution(self):
        result = evaluate_edit_candidate(
            self.candidate("unlisted_creator"),
            self.inspection(width=640, height=360, size_bytes=80_000, codec="unknown"),
            moderation={"has_music": True},
        )
        self.assertIn("source_below_policy_minimum_resolution", result.reasons)
        self.assertIn("brain_technical_quality_check_failed", result.reasons)

    def test_preserve_source_policy_does_not_add_square_conversion_warning(self):
        result = evaluate_edit_candidate(
            self.candidate("rickkzn_"),
            self.inspection(width=1080, height=1920),
            moderation={"has_music": True},
        )
        self.assertTrue(result.eligible, result.reasons)
        self.assertNotIn("needs_square_1080x1080_normalization", result.warnings)
        self.assertEqual(result.checks["output_format"], "preserve_source")

    def test_youtube_delivery_remains_paused_while_discovery_policy_stays_separate(self):
        self.assertFalse(delivery_enabled("youtube"))
        self.assertTrue(delivery_enabled("tiktok"))


if __name__ == "__main__":
    unittest.main()
