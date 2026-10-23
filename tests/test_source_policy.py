from __future__ import annotations
import unittest
from agent.brain.models import Candidate, InspectionResult
from agent.brain.policy import evaluate_edit_candidate, precheck_edit_candidate
from agent.source_policy import allow_upscale, delivery_enabled, download_quality_max, known_tiktok_profiles, load_source_policy_config, resolve_source_policy


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

    def test_poppy_or_playtime_substring_is_sufficient_without_edit_signal(self):
        for caption in ("poppy","PLAYTIME montage","a #PoPpYPlaytime tag"):
            with self.subTest(caption=caption):
                c=self.candidate("meloaep_",caption=caption)
                self.assertEqual(precheck_edit_candidate(c),())
                result=evaluate_edit_candidate(c,self.inspection(),moderation={"has_music":True})
                self.assertTrue(result.eligible,result.reasons)
                self.assertNotIn("is_edit",result.checks)
                self.assertNotIn("high_quality_edit",result.checks)

    def test_hashtag_keyword_works_but_unrelated_caption_rejects(self):
        c=self.candidate("rickkzn_",caption="new clip",hashtags=("#PLAYTIME",))
        self.assertTrue(evaluate_edit_candidate(c,self.inspection(),moderation={"has_music":True}).eligible)
        bad=self.candidate("rickkzn_",caption="unrelated gameplay")
        self.assertIn("required_keyword_missing",evaluate_edit_candidate(bad,self.inspection(),moderation={"has_music":True}).reasons)

    def test_no_audio_rejects_with_exact_reason(self):
        result=evaluate_edit_candidate(self.candidate("meloaep_"),self.inspection(audio=False),moderation={})
        self.assertIn("no_audio",result.reasons)

    def test_basic_minimum_quality_remains_and_upscale_is_disabled(self):
        result=evaluate_edit_candidate(self.candidate("meloaep_"),self.inspection(width=320,height=240),moderation={"has_music":True})
        self.assertIn("source_below_policy_minimum_resolution",result.reasons)
        self.assertEqual(download_quality_max(),1080)
        self.assertFalse(allow_upscale())
        self.assertFalse(delivery_enabled("youtube")); self.assertTrue(delivery_enabled("tiktok"))


if __name__=="__main__": unittest.main()
