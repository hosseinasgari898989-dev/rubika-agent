from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain.delivery import deliver_with_retry
from agent.brain.e2e_from_report import _select_candidates
from agent.brain.inspector import inspect_media, sha256_file
from agent.brain.models import Candidate, InspectionResult
from agent.brain.normalizer import NormalizeError, normalize_video_square_1080
from agent.brain.official import evaluate_official_media
from agent.brain.pipeline import stage_edit_batch
from agent.brain.policy import evaluate_edit_candidate, format_official_mob_caption


class BrainModelPolicyTests(unittest.TestCase):
    def candidate(self, **changes):
        values = dict(
            platform="tiktok",
            canonical_url="https://www.tiktok.com/@creator/video/123",
            external_id="123",
            title="Huggy Wuggy Poppy Playtime fan edit",
            caption="#PoppyPlaytime #edit",
            source_key="@creator",
            media_type="video",
            source_tier="unknown",
        )
        values.update(changes)
        return Candidate(**values)

    def inspection(self, **changes):
        values = dict(
            ok=True, media_type="video", sha256="a" * 64, size_bytes=1_000_000,
            mime_type="video/mp4", width=1080, height=1920, duration_seconds=12.5,
            has_audio=True, video_codec="h264", audio_codec="aac",
        )
        values.update(changes)
        return InspectionResult(**values)

    def moderation(self, **changes):
        values = dict(
            poppy_relevant=True, is_edit=True, insults_poppy=False,
            insults_other_games=False, high_effort=True, has_music=True,
        )
        values.update(changes)
        return values

    def test_metadata_cannot_replace_content_moderation(self):
        result = evaluate_edit_candidate(self.candidate(), self.inspection())
        self.assertEqual(result.decision, "needs_review")
        self.assertTrue(result.requires_review)
        self.assertIn("content_review_required", result.warnings)

    def test_good_candidate_with_explicit_moderation_is_eligible(self):
        result = evaluate_edit_candidate(
            self.candidate(), self.inspection(), moderation=self.moderation()
        )
        self.assertEqual(result.decision, "eligible")
        self.assertTrue(result.eligible)

    def test_duplicate_external_id_is_rejected(self):
        result = evaluate_edit_candidate(
            self.candidate(), self.inspection(), seen_external_ids={"123"},
            moderation=self.moderation(),
        )
        self.assertIn("duplicate_external_id", result.reasons)
        self.assertEqual(result.decision, "rejected")

    def test_duplicate_hash_is_rejected(self):
        result = evaluate_edit_candidate(
            self.candidate(), self.inspection(), seen_hashes={"a" * 64},
            moderation=self.moderation(),
        )
        self.assertIn("duplicate_file_hash", result.reasons)

    def test_insulting_metadata_is_rejected(self):
        result = evaluate_edit_candidate(
            self.candidate(title="Worst game ever - Poppy Playtime edit"),
            self.inspection(), moderation=self.moderation(),
        )
        self.assertIn("negative_or_insulting_text_signal", result.reasons)

    def test_content_moderation_rejects_insults_and_low_effort(self):
        insult = evaluate_edit_candidate(
            self.candidate(), self.inspection(),
            moderation=self.moderation(insults_poppy=True),
        )
        low_effort = evaluate_edit_candidate(
            self.candidate(), self.inspection(),
            moderation=self.moderation(high_effort=False),
        )
        self.assertIn("insults_poppy_character", insult.reasons)
        self.assertIn("low_effort_edit", low_effort.reasons)

    def test_image_or_audio_does_not_enter_edit_video_track(self):
        candidate = self.candidate(media_type="image")
        result = evaluate_edit_candidate(candidate, self.inspection(), moderation=self.moderation())
        self.assertIn("edit_track_requires_video", result.reasons)

    def test_official_caption_keeps_original_and_translation(self):
        caption = format_official_mob_caption("Official announcement", "اطلاعیه رسمی")
        self.assertEqual(
            caption,
            "ماب انترتینمنت پستی منتشر کرد و گفت:\nOfficial announcement\n\nترجمه متن:\nاطلاعیه رسمی",
        )

    def test_empty_official_caption_does_not_invent_text(self):
        self.assertIsNone(format_official_mob_caption(None, None))

    def test_candidate_rejects_non_https_urls(self):
        with self.assertRaises(ValueError):
            self.candidate(canonical_url="http://www.tiktok.com/@creator/video/123")

    def test_official_media_track_does_not_apply_edit_filters(self):
        candidate = self.candidate(
            platform="mob_official", canonical_url="https://www.mobentertainment.com/news",
            external_id="mob-1", title="Official news", media_type="image", source_tier="official",
        )
        result = evaluate_official_media(
            candidate,
            InspectionResult(True, "image", "b" * 64, 100_000, "image/jpeg", 1600, 900),
        )
        self.assertEqual(result.decision, "eligible")
        self.assertEqual(result.checks["track"], "official_mob_news")


class InspectorTests(unittest.TestCase):
    def test_image_file_is_verified_and_hashed(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.png"
            Image.new("RGB", (1080, 1080), (10, 20, 30)).save(path)
            result = inspect_media(path)
            self.assertTrue(result.ok)
            self.assertEqual(result.media_type, "image")
            self.assertEqual(result.width, 1080)
            self.assertEqual(result.height, 1080)
            self.assertEqual(result.sha256, sha256_file(path))

    def test_audio_media_requires_audio_stream(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.mp3"
            path.write_bytes(b"test")
            probe = {
                "streams": [{"codec_type": "audio", "codec_name": "mp3"}],
                "format": {"duration": "4.5"},
            }
            completed = subprocess.CompletedProcess([], 0, json.dumps(probe), "")
            with patch("agent.brain.inspector.subprocess.run", return_value=completed):
                result = inspect_media(path)
            self.assertTrue(result.ok)
            self.assertEqual(result.media_type, "audio")
            self.assertEqual(result.duration_seconds, 4.5)

    def test_invalid_video_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.mp4"
            path.write_bytes(b"not a video")
            completed = subprocess.CompletedProcess([], 1, "", "invalid")
            with patch("agent.brain.inspector.subprocess.run", return_value=completed):
                result = inspect_media(path)
            self.assertFalse(result.ok)
            self.assertIn("media_probe_failed", result.errors)


class PipelineTests(unittest.TestCase):
    def _candidate(self, platform, external_id):
        host = {
            "tiktok": "www.tiktok.com",
            "youtube": "www.youtube.com",
            "instagram": "www.instagram.com",
        }[platform]
        return Candidate(
            platform=platform,
            canonical_url=f"https://{host}/test/{external_id}",
            external_id=external_id,
            title="Poppy Playtime Huggy Wuggy edit",
            caption="#PoppyPlaytime #edit",
            source_key="@test",
            media_type="video",
        )

    @staticmethod
    def _moderation(_candidate, _path):
        return {
            "poppy_relevant": True, "is_edit": True, "insults_poppy": False,
            "insults_other_games": False, "high_effort": True, "has_music": True,
        }

    def test_pipeline_requires_every_source_count_before_batch_is_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            def download(candidate):
                path = Path(folder) / (candidate.platform + "-" + candidate.external_id + ".mp4")
                path.write_bytes((candidate.platform + candidate.external_id).encode())
                return path

            def inspect(path, kind=None):
                raw = Path(path).read_bytes()
                return InspectionResult(
                    True, "video", hashlib.sha256(raw).hexdigest(), len(raw), "video/mp4",
                    1080, 1920, 3.0, True, "h264", "aac",
                )

            result = stage_edit_batch(
                [self._candidate("tiktok", "t1"), self._candidate("youtube", "y1")],
                required_counts={"tiktok": 1, "youtube": 1},
                download=download, moderate=self._moderation,
                output_dir=Path(folder) / "normalized", inspector=inspect,
            )
            self.assertEqual(result.status, "ready")
            self.assertTrue(result.ready_for_review)
            self.assertFalse(result.ready_to_publish)
            self.assertEqual(len(result.staged), 2)

    def test_partial_batch_never_reports_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            def download(candidate):
                path = Path(folder) / "only-tiktok.mp4"
                path.write_bytes(b"one candidate")
                return path

            def inspect(path, kind=None):
                raw = Path(path).read_bytes()
                return InspectionResult(
                    True, "video", hashlib.sha256(raw).hexdigest(), len(raw), "video/mp4",
                    1080, 1920, 3.0, True, "h264", "aac",
                )

            result = stage_edit_batch(
                [self._candidate("tiktok", "t1")],
                required_counts={"tiktok": 1, "youtube": 1},
                download=download, moderate=self._moderation,
                output_dir=Path(folder) / "normalized", inspector=inspect,
            )
            self.assertEqual(result.status, "partial")
            self.assertFalse(result.ready_to_publish)


class DeliveryStateMachineTests(unittest.TestCase):
    def test_success_deletes_local_then_remote_only_after_success(self):
        events = []
        result = deliver_with_retry(
            item_id="tiktok:123",
            send=lambda: {"status": "OK", "message_id": "m1"},
            record_attempt=lambda n, status, code: events.append((n, status, code)),
            delete_local=lambda: events.append(("local", "deleted")),
            delete_remote=lambda: events.append(("remote", "deleted")),
            sleeper=lambda _: None,
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["cleanup"], "complete")
        self.assertLess(events.index(("local", "deleted")), events.index(("remote", "deleted")))

    def test_failed_local_cleanup_keeps_remote_copy(self):
        remote_deleted = []
        result = deliver_with_retry(
            item_id="tiktok:123",
            send=lambda: {"status": "OK"},
            record_attempt=lambda *_: None,
            delete_local=lambda: (_ for _ in ()).throw(OSError()),
            delete_remote=lambda: remote_deleted.append(True),
            sleeper=lambda _: None,
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["cleanup"], "pending")
        self.assertEqual(remote_deleted, [])

    def test_retries_definite_rejections_three_times_then_stops(self):
        events = []
        calls = []
        result = deliver_with_retry(
            item_id="instagram:abc",
            send=lambda: (calls.append(1) or {"status": "ERROR"}),
            record_attempt=lambda n, status, code: events.append((n, status, code)),
            delete_local=lambda: self.fail("must retain failed media"),
            max_attempts=3, sleeper=lambda _: None,
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(len(calls), 3)
        self.assertFalse(any(event[1] == "sent" for event in events))

    def test_ambiguous_response_stops_without_retry_or_delete(self):
        calls = []
        result = deliver_with_retry(
            item_id="tiktok:123",
            send=lambda: (calls.append(1) or {}),
            record_attempt=lambda *_: None,
            delete_local=lambda: self.fail("must retain uncertain media"),
            sleeper=lambda _: None,
        )
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(len(calls), 1)

    def test_transport_exception_is_uncertain_and_never_retried(self):
        calls = []
        result = deliver_with_retry(
            item_id="tiktok:123",
            send=lambda: (calls.append(1) or (_ for _ in ()).throw(TimeoutError())),
            record_attempt=lambda *_: None,
            delete_local=lambda: self.fail("must retain uncertain media"),
            sleeper=lambda _: None,
        )
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(len(calls), 1)

    def test_attempt_count_cannot_exceed_three(self):
        with self.assertRaises(ValueError):
            deliver_with_retry(
                item_id="x", send=lambda: {"status": "ERROR"},
                record_attempt=lambda *_: None, delete_local=lambda: None,
                max_attempts=4,
            )


class NormalizerGuardTests(unittest.TestCase):
    def test_normalizer_refuses_same_source_and_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "video.mp4"
            file.write_bytes(b"not a real video")
            with self.assertRaises(NormalizeError):
                normalize_video_square_1080(file, file)


class E2ECandidateSelectionTests(unittest.TestCase):
    def test_candidate_limit_and_offset_are_respected(self):
        rows = [
            {
                "id": str(index),
                "url": f"https://www.tiktok.com/@creator/video/{index}",
                "title": f"Poppy Playtime fan edit {index}",
                "caption": "#PoppyPlaytime #edit",
                "author": "@creator",
            }
            for index in range(1, 4)
        ]
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "discovery.json"
            report.write_text(
                json.dumps({"candidates": {"tiktok": rows}}),
                encoding="utf-8",
            )
            selected = _select_candidates(
                report, "tiktok", set(), set(), limit=1, offset=1
            )
        self.assertEqual([candidate.external_id for candidate in selected], ["2"])


if __name__ == "__main__":
    unittest.main()
