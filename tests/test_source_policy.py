from __future__ import annotations
import unittest
from agent.brain.models import Candidate, InspectionResult
from agent.brain.policy import evaluate_edit_candidate, precheck_edit_candidate
from agent.source_policy import allow_upscale, delivery_enabled, download_quality_max, known_tiktok_profiles, load_source_policy_config, resolve_source_policy, set_dynamic_tiktok_sources


class TikTokSourcePolicyTests(unittest.TestCase):
    def candidate(self, handle, caption="Poppy Playtime clip", hashtags=()):
        return Candidate(platform="tiktok", canonical_url=f"https://www.tiktok.com/@{handle}/video/7616247520657771796",
            external_id="7616247520657771796", title="clip", caption=caption, source_key=handle,
            media_type="video", hashtags=tuple(hashtags), source_policy=resolve_source_policy("tiktok", handle))

    @staticmethod
    def inspection(width=1920, height=1080, size_bytes=1_000_000, duration=14.0, audio=True):
        return InspectionResult(ok=True, media_type="video", sha256="a"*64, size_bytes=size_bytes,
            mime_type="video/mp4", width=width, height=height, duration_seconds=duration,
            has_audio=audio, video_codec="h264", audio_codec="aac" if audio else None)

    def test_only_six_selected_profiles_exist_and_no_unknown_default(self):
        config=load_source_policy_config()
        self.assertEqual(known_tiktok_profiles(),("meloaep_","smyxae","menezxl9","rickkzn_","saylex.aep812","poppytv_ae"))
        self.assertEqual([row["handle"] for row in config["sources"]],list(known_tiktok_profiles()))
        self.assertNotIn("unknown_source_defaults",config)
        self.assertNotIn("unknown_source_max_results",config.get("discovery",{}))
        self.assertEqual(config["required_any_keywords"],["poppy","playtime"])

    def test_channel_formats_and_minimum_resolution_remain(self):
        square={"meloaep_","smyxae","menezxl9"}
        for handle in known_tiktok_profiles():
            policy=resolve_source_policy("tiktok",handle)
            self.assertEqual(policy["minimum_source_short_side"],480)
            self.assertEqual(policy["output_format"],"square_1080x1080" if handle in square else "preserve_source")
            self.assertNotIn("require_edit_signal_check",policy)
            self.assertNotIn("require_brain_quality_check",policy)

    def test_unlisted_source_is_disabled_not_given_unknown_quality_filters(self):
        policy=resolve_source_policy("tiktok","unlisted_creator")
        self.assertFalse(policy["known_source"]); self.assertFalse(policy["enabled"])
        self.assertEqual(policy["trust_tier"],"unselected"); self.assertIsNone(policy["output_format"])
        self.assertEqual(policy["minimum_source_short_side"],0)
        self.assertIn("source_not_selected",precheck_edit_candidate(self.candidate("unlisted_creator")))

    def test_missing_edit_signal_enters_review_only_after_all_media_gates_pass(self):
        for caption in ("poppy","PLAYTIME montage","a #PoPpYPlaytime tag"):
            with self.subTest(caption=caption):
                candidate = self.candidate("meloaep_", caption=caption)
                self.assertEqual(precheck_edit_candidate(candidate), ())
                result = evaluate_edit_candidate(
                    candidate, self.inspection(), moderation={"has_music": True},
                )
                self.assertIn("edit_signal_not_found_by_brain", result.reasons)
                self.assertFalse(result.eligible)

        edit = self.candidate("meloaep_", caption="Poppy Playtime velocity edit")
        accepted = evaluate_edit_candidate(edit, self.inspection(), moderation={"has_music": True})
        self.assertTrue(accepted.eligible, accepted.reasons)

    def test_off_topic_low_resolution_no_audio_and_duplicate_never_enter_edit_review(self):
        moderation = {"has_music": True}
        off_topic = evaluate_edit_candidate(
            self.candidate("rickkzn_", caption="unrelated gameplay"),
            self.inspection(), moderation=moderation,
        )
        self.assertIn("required_keyword_missing", off_topic.reasons)
        self.assertNotIn("edit_signal_not_found_by_brain", off_topic.reasons)

        low_resolution = evaluate_edit_candidate(
            self.candidate("meloaep_", caption="Poppy Playtime clip"),
            self.inspection(width=320, height=240), moderation=moderation,
        )
        self.assertIn("source_below_policy_minimum_resolution", low_resolution.reasons)
        self.assertNotIn("edit_signal_not_found_by_brain", low_resolution.reasons)

        silent = evaluate_edit_candidate(
            self.candidate("meloaep_", caption="Poppy Playtime clip"),
            self.inspection(audio=False), moderation={},
        )
        self.assertIn("no_audio", silent.reasons)
        self.assertNotIn("edit_signal_not_found_by_brain", silent.reasons)

        duplicate = evaluate_edit_candidate(
            self.candidate("meloaep_", caption="Poppy Playtime clip"),
            self.inspection(), seen_external_ids={"7616247520657771796"}, moderation=moderation,
        )
        self.assertIn("duplicate_external_id", duplicate.reasons)
        self.assertNotIn("edit_signal_not_found_by_brain", duplicate.reasons)

    def test_hashtag_keyword_works_and_edit_marker_is_still_required_for_delivery(self):
        candidate = self.candidate("rickkzn_", caption="new clip", hashtags=("#PLAYTIME",))
        result = evaluate_edit_candidate(candidate, self.inspection(), moderation={"has_music": True})
        self.assertIn("edit_signal_not_found_by_brain", result.reasons)
        marked = self.candidate("rickkzn_", caption="new clip", hashtags=("#PLAYTIMEedit",))
        self.assertTrue(evaluate_edit_candidate(marked, self.inspection(), moderation={"has_music": True}).eligible)

    def test_no_audio_rejects_with_exact_reason(self):
        result=evaluate_edit_candidate(self.candidate("meloaep_"),self.inspection(audio=False),moderation={})
        self.assertIn("no_audio",result.reasons)

    def test_console_managed_sources_use_existing_tiktok_filters_and_soft_disable(self):
        rows = [
            {"platform": "tiktok", "source_key": "newcreator", "enabled": 1, "managed": True},
        ]
        set_dynamic_tiktok_sources(rows)
        enabled = resolve_source_policy("tiktok", "newcreator")
        self.assertTrue(enabled["known_source"])
        self.assertTrue(enabled["enabled"])
        self.assertEqual(enabled["minimum_source_short_side"], 480)
        self.assertEqual(enabled["output_format"], "preserve_source")
        self.assertTrue(enabled["require_poppy_relevance_check"])
        disabled = {"platform": "tiktok", "source_key": "newcreator", "enabled": 0, "managed": True}
        set_dynamic_tiktok_sources([disabled])
        self.assertFalse(resolve_source_policy("tiktok", "newcreator")["enabled"])
        set_dynamic_tiktok_sources([], include_disabled=True)

    def test_basic_minimum_quality_remains_and_upscale_is_disabled(self):
        result=evaluate_edit_candidate(self.candidate("meloaep_"),self.inspection(width=320,height=240),moderation={"has_music":True})
        self.assertIn("source_below_policy_minimum_resolution",result.reasons)
        self.assertEqual(download_quality_max(),1080)
        self.assertFalse(allow_upscale())
        self.assertFalse(delivery_enabled("youtube")); self.assertTrue(delivery_enabled("tiktok"))


if __name__=="__main__": unittest.main()
