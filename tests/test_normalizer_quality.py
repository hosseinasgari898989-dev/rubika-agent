from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from agent.brain.models import Candidate, InspectionResult
from agent.brain.normalizer import NormalizeError, normalize_video_square_1080
from agent.brain.pipeline import stage_edit_batch
from agent.source_policy import resolve_source_policy


def inspection(width=720, height=720):
    return InspectionResult(ok=True,media_type="video",sha256="a"*64,size_bytes=200_000,
        mime_type="video/mp4",width=width,height=height,duration_seconds=14.0,
        has_audio=True,video_codec="h264",audio_codec="aac")


class NoUpscaleQualityTests(unittest.TestCase):
    def test_direct_square_normalizer_rejects_upscaling_low_resolution_source(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/"source.mp4"; source.write_bytes(b"video")
            target=Path(temp)/"target.mp4"
            with patch("agent.brain.normalizer.inspect_media",return_value=inspection()), patch("agent.brain.normalizer.allow_upscale",return_value=False), patch("agent.brain.normalizer.subprocess.run") as run:
                with self.assertRaisesRegex(NormalizeError,"upscale_disallowed_native_source_required"):
                    normalize_video_square_1080(source,target)
            run.assert_not_called()
            self.assertFalse(target.exists())

    def test_pipeline_keeps_sub_1080_source_at_native_dimensions_and_persists_them(self):
        candidate=Candidate(platform="tiktok",canonical_url="https://www.tiktok.com/@meloaep_/video/7616247520657771796",
            external_id="7616247520657771796",title="clip",caption="poppy playtime",source_key="meloaep_",
            media_type="video",source_policy=resolve_source_policy("tiktok","meloaep_"))
        with tempfile.TemporaryDirectory() as temp:
            src=Path(temp)/"source.mp4";src.write_bytes(b"native source video bytes")
            output=Path(temp)/"out"
            normalizer=Mock()
            staged_result=stage_edit_batch([candidate],required_counts={"tiktok":1},
                download=lambda _candidate:src,moderate=lambda *_args:{"has_music":True},
                output_dir=output,inspector=lambda *_args:inspection(),normalizer=normalizer)
            self.assertEqual(staged_result.status,"ready",staged_result.skipped)
            self.assertEqual(len(staged_result.staged),1)
            item=staged_result.staged[0]
            self.assertEqual(item.publish_path,str(src))
            self.assertEqual(item.inspection.width,720)
            normalizer.assert_not_called()
            from agent.brain.cloud_state import CloudState
            payload=CloudState.prepare_staged_payload(item)
            self.assertEqual(payload["validation"]["downloaded_width"],720)
            self.assertEqual(payload["validation"]["downloaded_height"],720)


if __name__=="__main__":unittest.main()
