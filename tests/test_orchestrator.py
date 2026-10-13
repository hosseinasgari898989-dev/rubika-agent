from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain.authorization import SourceRegistry
from agent.brain.cloud_state import CloudStateError
from agent.brain.ledger import new_state
from agent.brain.models import Candidate, Evaluation, InspectionResult
from agent.brain.orchestrator import StagingOrchestrator
from agent.brain.pipeline import BatchResult, StagedMedia


TOKEN = "orchestrator-test-token-with-at-least-thirty-two-characters"


def make_candidate(external_id="vkVFgmgLo1s", source_key="UC_allowed", title="Poppy Playtime edit"):
    return Candidate(
        platform="youtube",
        canonical_url=f"https://www.youtube.com/watch?v={external_id}",
        external_id=external_id,
        title=title,
        caption="#PoppyPlaytime #edit",
        source_key=source_key,
        media_type="video",
        source_tier="unknown",
    )


def make_staged(candidate=None):
    candidate = candidate or make_candidate()
    inspection = InspectionResult(
        ok=True,
        media_type="video",
        sha256="a" * 64,
        size_bytes=1_000_000,
        mime_type="video/mp4",
        width=1080,
        height=1080,
        duration_seconds=12.5,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
    )
    evaluation = Evaluation(
        decision="eligible",
        score=100,
        requires_review=False,
        checks={
            "poppy_relevant": True,
            "is_edit": True,
            "insults_poppy": False,
            "insults_other_games": False,
            "high_effort": True,
            "has_music": True,
        },
    )
    return StagedMedia(
        candidate=candidate,
        original_path="/tmp/raw.mp4",
        publish_path="/tmp/square.mp4",
        inspection=inspection,
        evaluation=evaluation,
        storage_key="/Rubika-Agent/staging/youtube_vkVFgmgLo1s.mp4",
        caption=candidate.caption,
    )


class FakeLedger:
    def __init__(self):
        self.state = new_state()
        self.saves = 0

    def load(self):
        return copy.deepcopy(self.state)

    def save(self, state):
        self.state = copy.deepcopy(dict(state))
        self.saves += 1
        return {"ok": True, "sha256": "b" * 64}


class FakeCloud:
    def __init__(self, *, upsert_fails=False, seen=None):
        self.upsert_fails = upsert_fails
        self.seen = seen or {"ids": set(), "hashes": set()}
        self.runs = {}
        self.items = {}
        self.events = []

    def create_run(self, *, trigger_type="manual", run_id=None):
        self.runs[run_id] = {"status": "running", "trigger_type": trigger_type}
        return run_id

    def get_seen_index(self):
        return {
            "ids": set(self.seen["ids"]),
            "hashes": set(self.seen["hashes"]),
        }

    def prepare_staged_payload(self, staged):
        from agent.brain.cloud_state import CloudState
        return CloudState.prepare_staged_payload(staged)

    def upsert_item(self, payload):
        if self.upsert_fails:
            raise CloudStateError("control_api_unreachable")
        self.items[payload["platform"] + ":" + payload["external_id"]] = copy.deepcopy(dict(payload))
        return {"ok": True}

    def finish_run(self, run_id, *, status, **kwargs):
        self.runs[run_id]["status"] = status
        return {"ok": True}

    def add_event(self, code, **kwargs):
        self.events.append({"code": code, **kwargs})
        return {"ok": True}


def allowed_registry():
    return SourceRegistry.from_mapping({
        "version": 1,
        "sources": [{
            "platform": "youtube",
            "source_key": "UC_allowed",
            "permission_basis": "explicit_permission",
            "permission_confirmed": True,
            "enabled": True,
        }],
        "items": [],
    })


class StagingOrchestratorTests(unittest.TestCase):
    def test_missing_moderation_adapter_stops_before_download(self):
        cloud = FakeCloud()
        ledger = FakeLedger()
        downloads = []
        orchestrator = StagingOrchestrator(
            cloud=cloud,
            storage=object(),
            registry=allowed_registry(),
            downloaders={"youtube": lambda *args: downloads.append(args)},
            moderator=None,
            output_dir=tempfile.mkdtemp(prefix="orchestrator-no-moderator-"),
            ledger=ledger,
            required_counts={"youtube": 1},
        )
        result = orchestrator.run([make_candidate()])
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.publishing)
        self.assertEqual(downloads, [])
        self.assertIn("moderation_not_configured", result.cloud_sync_errors)
        self.assertEqual(cloud.runs[result.run_id]["status"], "failed")

    def test_only_explicitly_authorized_sources_reach_staging(self):
        cloud = FakeCloud()
        ledger = FakeLedger()
        staged_inputs = []
        allowed = make_candidate()
        denied = make_candidate("n38wy3o0DhY", "UC_unknown", "Poppy Playtime edit")

        def fake_stage(candidates, **kwargs):
            staged_inputs.extend(candidates)
            return BatchResult(status="partial", required_counts={"youtube": 1}, skipped=[])

        with tempfile.TemporaryDirectory() as folder:
            orchestrator = StagingOrchestrator(
                cloud=cloud,
                storage=object(),
                registry=allowed_registry(),
                downloaders={"youtube": lambda *_: self.fail("mock staging must not download")},
                moderator=lambda *_: {
                    "poppy_relevant": True, "is_edit": True, "insults_poppy": False,
                    "insults_other_games": False, "high_effort": True, "has_music": True,
                },
                output_dir=folder,
                ledger=ledger,
                required_counts={"youtube": 1},
            )
            with patch("agent.brain.orchestrator.stage_edit_batch", side_effect=fake_stage):
                result = orchestrator.run([allowed, denied])

        self.assertEqual(result.authorized_count, 1)
        self.assertEqual([item.external_id for item in staged_inputs], ["vkVFgmgLo1s"])
        self.assertEqual(staged_inputs[0].source_tier, "trusted")
        self.assertTrue(any(row["reason"] == "source_not_authorized" for row in result.skipped))
        self.assertFalse(result.publishing)

    def test_d1_sync_failure_preserves_media_metadata_for_later_recovery(self):
        cloud = FakeCloud(upsert_fails=True)
        ledger = FakeLedger()
        candidate = make_candidate()
        staged = make_staged(candidate)

        def fake_stage(candidates, **kwargs):
            self.assertEqual(len(list(candidates)), 1)
            kwargs["persist_staged"](staged)
            return BatchResult(
                status="ready",
                required_counts={"youtube": 1},
                staged=[staged],
                skipped=[],
            )

        with tempfile.TemporaryDirectory() as folder:
            orchestrator = StagingOrchestrator(
                cloud=cloud,
                storage=object(),
                registry=allowed_registry(),
                downloaders={"youtube": lambda *_: self.fail("mock staging must not download")},
                moderator=lambda *_: {},
                output_dir=folder,
                ledger=ledger,
                required_counts={"youtube": 1},
            )
            with patch("agent.brain.orchestrator.stage_edit_batch", side_effect=fake_stage):
                result = orchestrator.run([candidate])

            self.assertEqual(result.status, "partial")
            self.assertEqual(len(result.cloud_sync_errors), 1)
            record = ledger.state["items"]["youtube:vkVFgmgLo1s"]
            self.assertEqual(record["cloud_sync"], "pending")
            self.assertEqual(record["storage_key"], "/Rubika-Agent/staging/youtube_vkVFgmgLo1s.mp4")
            self.assertEqual(record["cloud_payload"]["review_state"], "pending")
            self.assertFalse(result.publishing)

            cloud.upsert_fails = False
            retry = orchestrator.retry_pending_cloud_sync()
            self.assertTrue(retry["ok"])
            self.assertEqual(retry["completed"], ["youtube:vkVFgmgLo1s"])
            self.assertEqual(ledger.state["items"]["youtube:vkVFgmgLo1s"]["cloud_sync"], "ok")
            self.assertIn("youtube:vkVFgmgLo1s", cloud.items)


if __name__ == "__main__":
    unittest.main()
