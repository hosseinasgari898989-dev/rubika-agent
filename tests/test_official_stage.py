from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.brain.authorization import SourceRegistry
from agent.brain.inspector import InspectionResult
from agent.brain.ledger import new_state
from agent.brain.models import Candidate
from agent.brain.official_stage import stage_official_media


class FakeStorage:
    def __init__(self):
        self.uploads = []

    def upload(self, local_path, relative_dir="staging"):
        self.uploads.append((str(local_path), relative_dir))
        return "/Rubika-Agent/" + relative_dir + "/" + Path(local_path).name


class FakeCloud:
    def __init__(self, fail=False):
        self.fail = fail
        self.items = []

    @staticmethod
    def prepare_staged_payload(staged):
        from agent.brain.cloud_state import CloudState
        return CloudState.prepare_staged_payload(staged)

    def upsert_item(self, payload):
        if self.fail:
            raise RuntimeError("D1 test outage")
        self.items.append(payload)
        return {"ok": True}


class OfficialStageTests(unittest.TestCase):
    def candidate(self, **changes):
        values = {
            "platform": "mob_official",
            "canonical_url": "https://www.mobentertainment.com/news/announcement-1",
            "external_id": "announcement-1",
            "title": "Mob Entertainment announcement",
            "caption": "Original official post",
            "source_key": "mobentertainment.com",
            "media_type": "image",
            "source_tier": "unknown",
            "original_caption": "Original official post",
            "persian_translation": "متن رسمی",
        }
        values.update(changes)
        return Candidate(**values)

    def registry(self, *, enabled=True, confirmed=True):
        return SourceRegistry.from_mapping({
            "version": 1,
            "sources": [{
                "platform": "mob_official",
                "source_key": "mobentertainment.com",
                "permission_basis": "license",
                "permission_confirmed": confirmed,
                "enabled": enabled,
            }],
            "items": [],
        })

    def test_official_image_uses_separate_track_and_preserves_bilingual_caption(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "news.png"
            file.write_bytes(b"public sample file")
            storage = FakeStorage()
            cloud = FakeCloud()
            ledger = new_state()
            saves = []
            inspected = InspectionResult(
                True, "image", "a" * 64, file.stat().st_size,
                "image/png", 1600, 900,
            )
            result = stage_official_media(
                [self.candidate()],
                registry=self.registry(),
                download=lambda _candidate: file,
                storage=storage,
                cloud=cloud,
                ledger_state=ledger,
                output_dir=folder,
                inspector=lambda _path, _kind: inspected,
                persist_ledger=lambda state: saves.append(state.copy()),
            )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["staged_count"], 1)
        self.assertFalse(result["publishing"])
        staged = result["staged"][0]
        self.assertEqual(staged.candidate.source_tier, "official")
        self.assertIn("Original official post", staged.caption)
        self.assertIn("متن رسمی", staged.caption)
        self.assertEqual(cloud.items[0]["review_state"], "pending")
        self.assertEqual(cloud.items[0]["source_tier"], "official")
        self.assertEqual(cloud.items[0]["media_type"], "image")
        self.assertEqual(storage.uploads[0][1], "official")
        self.assertTrue(saves)

    def test_unpermitted_official_source_is_rejected_before_download(self):
        calls = []
        result = stage_official_media(
            [self.candidate()],
            registry=SourceRegistry.from_mapping({"version": 1, "sources": [], "items": []}),
            download=lambda candidate: calls.append(candidate),
            storage=FakeStorage(),
            cloud=FakeCloud(),
            ledger_state=new_state(),
            output_dir=tempfile.mkdtemp(prefix="official-permission-"),
        )
        self.assertEqual(calls, [])
        self.assertEqual(result["staged_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "source_not_authorized")

    def test_carousel_is_not_misreported_as_single_image(self):
        calls = []
        result = stage_official_media(
            [self.candidate(media_type="carousel")],
            registry=self.registry(),
            download=lambda candidate: calls.append(candidate),
            storage=FakeStorage(),
            cloud=FakeCloud(),
            ledger_state=new_state(),
            output_dir=tempfile.mkdtemp(prefix="official-carousel-"),
        )
        self.assertEqual(calls, [])
        self.assertEqual(result["skipped"][0]["reason"], "carousel_requires_album_adapter")

    def test_missing_caption_and_translation_are_not_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "news.png"
            file.write_bytes(b"public sample file")
            result = stage_official_media(
                [self.candidate(caption="", original_caption=None, persian_translation=None)],
                registry=self.registry(),
                download=lambda _candidate: file,
                storage=FakeStorage(),
                cloud=FakeCloud(),
                ledger_state=new_state(),
                output_dir=folder,
                inspector=lambda _path, _kind: InspectionResult(
                    True, "image", "c" * 64, file.stat().st_size, "image/png", 800, 600,
                ),
            )
        self.assertEqual(result["staged"][0].caption, "")

    def test_cloud_sync_failure_keeps_a_recoverable_ledger_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "news.png"
            file.write_bytes(b"public sample file")
            ledger = new_state()
            result = stage_official_media(
                [self.candidate()],
                registry=self.registry(),
                download=lambda _candidate: file,
                storage=FakeStorage(),
                cloud=FakeCloud(fail=True),
                ledger_state=ledger,
                output_dir=folder,
                inspector=lambda _path, _kind: InspectionResult(
                    True, "image", "d" * 64, file.stat().st_size, "image/png", 1600, 900,
                ),
            )
        self.assertEqual(result["status"], "partial")
        item = ledger["items"]["mob_official:announcement-1"]
        self.assertEqual(item["cloud_sync"], "pending")
        self.assertEqual(item["cloud_payload"]["review_state"], "pending")
        self.assertEqual(len(result["cloud_sync_errors"]), 1)


if __name__ == "__main__":
    unittest.main()
