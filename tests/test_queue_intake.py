from __future__ import annotations
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from agent.brain.queue_intake import candidates_for_queue, fair_round_robin, run_intake
from agent.source_policy import known_tiktok_profiles

PROFILES = known_tiktok_profiles()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _video_id(creator_index, video_index):
    return f"769{creator_index:02d}{video_index:02d}000000000000"


def _row(external_id, author, *, title="Poppy Playtime edit"):
    return {"id": external_id, "url": f"https://www.tiktok.com/@{author}/video/{external_id}?is_from_webapp=1",
            "author": author, "title": title, "caption": title, "hashtags": ["poppyplaytime"],
            "published_at": _now_iso(), "media_type": "video", "freshness_verified": True}


class FakeCloudState:
    def __init__(self, *_args, **_kwargs):
        self.items = {status: [] for status in ("queued", "processing", "sent", "failed", "uncertain", "skipped")}
        self.items["queued"].append({"id": "tiktok:" + _video_id(5,99), "platform": "tiktok",
            "external_id": _video_id(5,99), "source_key": PROFILES[5], "status": "queued",
            "created_at": "2026-10-14T01:00:00Z"})
        self.blocking_seen_ids = {"tiktok:" + _video_id(0,99)}
        self.duplicate_once = {_video_id(2,0)}

    def get_automation_queue(self, *, status="queued", limit=100, offset=0):
        values = self.items[status]
        return {"ok": True, "items": values[offset:offset + limit]}

    def get_seen_index(self, **_kwargs):
        return {"ids": set(self.blocking_seen_ids), "hashes": set()}

    def enqueue_automation_items(self, rows):
        row = rows[0]; external_id = row["external_id"]
        existing = {item.get("external_id") for group in self.items.values() for item in group}
        if external_id in existing or external_id in self.duplicate_once:
            self.duplicate_once.discard(external_id)
            return {"received_count": 1, "inserted_count": 0, "duplicate_count": 1, "invalid_count": 0}
        self.items["queued"].append({"id": row["platform"] + ":" + external_id, "platform": row["platform"],
            "external_id": external_id, "canonical_url": row["canonical_url"], "source_key": row["source_key"],
            "status": "queued", "created_at": _now_iso()})
        return {"received_count": 1, "inserted_count": 1, "duplicate_count": 0, "invalid_count": 0}

    def get_automation_queue_summary(self):
        counts = {key: len(values) for key, values in self.items.items()}
        counts["sent_today"] = counts["sent"]; counts["usage_day"] = datetime.now(timezone.utc).date().isoformat()
        return counts


class QueueIntakeTests(unittest.TestCase):
    def test_maps_metadata_deduplicates_and_strips_tracking_query(self):
        video_id = "7616247520657771796"
        tracked = _row(video_id, PROFILES[0])
        plain = _row(video_id, PROFILES[0])
        plain["url"] = f"https://www.tiktok.com/@{PROFILES[0]}/video/{video_id}"
        rows = candidates_for_queue({"candidates": {"tiktok": [tracked, plain]}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["external_id"], video_id)
        self.assertEqual(rows[0]["source_key"], PROFILES[0])
        self.assertEqual(rows[0]["canonical_url"], f"https://www.tiktok.com/@{PROFILES[0]}/video/{video_id}")
        self.assertTrue(all("media_bytes" not in row and "path" not in row for row in rows))

    def test_only_six_selected_sources_can_enter_queue(self):
        diagnostics = {}
        rows = candidates_for_queue({"candidates": {"tiktok": [
            _row("7616247520657771796", "unlisted_creator"), _row("7616247520657771797", PROFILES[0]),
        ]}}, diagnostics=diagnostics)
        self.assertEqual([row["source_key"] for row in rows], [PROFILES[0]])
        self.assertEqual(diagnostics["rejected_by_reason"].get("source_not_selected"), 1)

    def test_caption_or_hashtags_must_contain_keyword_and_edit_signal_is_not_required(self):
        caption = _row("7616247520657771796", PROFILES[0], title="clip")
        caption["caption"] = "PlayTIME compilation"; caption["hashtags"] = []
        edit_only = _row("7616247520657771797", PROFILES[1], title="clip")
        edit_only["caption"] = "velocity aep transitions"; edit_only["hashtags"] = []
        hashtag_match = _row("7616247520657771798", PROFILES[2], title="clip")
        hashtag_match["caption"] = "new clip"; hashtag_match["hashtags"] = ["#POPPYfan"]
        rows = candidates_for_queue({"candidates": {"tiktok": [caption, edit_only, hashtag_match]}})
        self.assertEqual([row["external_id"] for row in rows], ["7616247520657771796", "7616247520657771798"])

    def test_missing_timestamp_is_deferred_to_phase_six_but_non_numeric_ids_reject(self):
        undated = _row("7616247520657771796", PROFILES[0]); undated["published_at"] = None
        invalid = _row("not-numeric", PROFILES[0])
        rows = candidates_for_queue({"candidates": {"tiktok": [undated, invalid]}})
        self.assertEqual([row["external_id"] for row in rows], ["7616247520657771796"])
        self.assertIsNone(rows[0]["published_at"])

    def test_old_timestamp_is_not_filtered_before_phase_six_date_engine(self):
        row = _row("7616247520657771796", PROFILES[0]); row["published_at"] = "2020-01-01T00:00:00Z"
        rows = candidates_for_queue({"candidates": {"tiktok": [row]}}, now=datetime(2026,10,23,12,tzinfo=timezone.utc))
        self.assertEqual([item["external_id"] for item in rows], ["7616247520657771796"])

    def test_round_robin_distributes_selected_creators_and_rotates(self):
        rows = []
        for i, creator in enumerate(PROFILES):
            rows.extend([{"external_id": str(i)+"1", "source_key": creator}, {"external_id": str(i)+"2", "source_key": creator}])
        self.assertEqual([row["external_id"] for row in fair_round_robin(rows)[:4]], ["01","11","21","31"])
        self.assertEqual([row["external_id"] for row in fair_round_robin(rows, last_source_key=PROFILES[1])[:3]], ["21","31","41"])

    def test_intake_skips_existing_ids_refills_duplicates_and_caps_four_new_items(self):
        candidates = []
        for i, creator in enumerate(PROFILES):
            candidates.extend(_row(_video_id(i,j),creator) for j in range(4))
        candidates.insert(0,_row(_video_id(0,99),PROFILES[0])); candidates.append(_row(_video_id(5,99),PROFILES[5]))
        payload = {"schema_version":1,"source":"termux_phone","mode":"metadata_only_no_download_no_publish",
                   "generated_at":_now_iso(),"candidates":{"tiktok":candidates,"mob_official":[]}}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"report.json"; path.write_text(json.dumps(payload),encoding="utf-8")
            with patch("agent.brain.queue_intake.CloudState",FakeCloudState):
                result=run_intake(path,max_new_items=4)
        self.assertTrue(result["ok"]); self.assertEqual(result["inserted_count"],4)
        self.assertEqual(result["max_new_items_per_run"],4); self.assertEqual(len(result["selected_items"]),4)
        self.assertEqual(len({row["source_key"] for row in result["selected_items"]}),4)
        ids={row["external_id"] for row in result["selected_items"]}
        self.assertNotIn(_video_id(0,99),ids); self.assertNotIn(_video_id(5,99),ids)
        self.assertEqual(result["duplicate_count"],1)
        self.assertTrue(all(row["queue_status"]=="queued" for row in result["selected_items"]))


if __name__ == "__main__":
    unittest.main()
