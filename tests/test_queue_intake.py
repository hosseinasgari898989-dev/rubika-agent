from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent.brain.queue_intake import (
    _is_poppy_related,
    _visible_metadata_text,
    candidates_for_queue,
    fair_round_robin,
    run_intake,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _video_id(creator_index: int, video_index: int) -> str:
    return f"769{creator_index:02d}{video_index:02d}000000000000"


def _row(external_id: str, author: str, *, title: str = "Poppy Playtime edit") -> dict:
    return {
        "id": external_id,
        "url": f"https://www.tiktok.com/@{author}/video/{external_id}",
        "author": author,
        "title": title,
        "caption": "Poppy Playtime edit",
        "hashtags": ["poppyplaytime"],
        "published_at": _now_iso(),
        "media_type": "video",
        "freshness_verified": True,
    }


class FakeCloudState:
    def __init__(self, *_args, **_kwargs):
        self.items: dict[str, list[dict]] = {
            "queued": [],
            "processing": [],
            "sent": [],
            "failed": [],
            "uncertain": [],
            "skipped": [],
        }
        old = {
            "id": "tiktok:" + _video_id(5, 99),
            "platform": "tiktok",
            "external_id": _video_id(5, 99),
            "source_key": "creator5",
            "status": "queued",
            "created_at": "2026-10-14T01:00:00Z",
        }
        self.items["queued"].append(old)
        self.blocking_seen_ids = {"tiktok:" + _video_id(0, 99)}
        self.duplicate_once = {_video_id(2, 0)}

    def get_automation_queue(self, *, status: str = "queued", limit: int = 100, offset: int = 0):
        values = self.items[status]
        return {"ok": True, "items": values[offset:offset + limit]}

    def get_seen_index(self, **_kwargs):
        return {"ids": set(self.blocking_seen_ids), "hashes": set()}

    def enqueue_automation_items(self, rows: list[dict]):
        row = rows[0]
        external_id = row["external_id"]
        existing = {
            item.get("external_id")
            for group in self.items.values()
            for item in group
        }
        if external_id in existing or external_id in self.duplicate_once:
            self.duplicate_once.discard(external_id)
            return {
                "received_count": 1, "inserted_count": 0,
                "duplicate_count": 1, "invalid_count": 0,
            }
        created = {
            "id": row["platform"] + ":" + external_id,
            "platform": row["platform"],
            "external_id": external_id,
            "canonical_url": row["canonical_url"],
            "source_key": row["source_key"],
            "status": "queued",
            "created_at": _now_iso(),
        }
        self.items["queued"].append(created)
        return {
            "received_count": 1, "inserted_count": 1,
            "duplicate_count": 0, "invalid_count": 0,
        }

    def get_automation_queue_summary(self):
        counts = {key: len(values) for key, values in self.items.items()}
        counts["sent_today"] = counts["sent"]
        counts["usage_day"] = datetime.now(timezone.utc).date().isoformat()
        return counts


class QueueIntakeTests(unittest.TestCase):
    def test_maps_metadata_and_deduplicates_without_media_bytes(self):
        payload = {
            "schema_version": 1,
            "source": "termux_phone",
            "mode": "metadata_only_no_download_no_publish",
            "candidates": {
                "tiktok": [
                    _row("7616247520657771796", "creator"),
                    _row("7616247520657771796", "creator"),
                ],
                "youtube": [
                    {
                        "id": "abcdefghijk",
                        "url": "https://www.youtube.com/shorts/abcdefghijk",
                        "author": "official_creator",
                        "title": "Poppy edit",
                        "published_at": _now_iso(),
                        "freshness_verified": True,
                    }
                ],
                "instagram": [
                    {
                        "id": "AbCdEf_1234",
                        "url": "https://www.instagram.com/reel/AbCdEf_1234/",
                        "title": "Poppy Playtime edit",
                        "caption": "Poppy Playtime",
                        "published_at": _now_iso(),
                        "media_type": "video",
                    }
                ],
            },
        }
        rows = candidates_for_queue(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual([row["platform"] for row in rows], ["tiktok"])
        self.assertEqual(rows[0]["external_id"], "7616247520657771796")
        self.assertEqual(rows[0]["source_key"], "creator")
        self.assertTrue(rows[0]["freshness_verified"])
        self.assertEqual(rows[0]["published_at"][:4], str(datetime.now(timezone.utc).year))
        self.assertTrue(all("media_bytes" not in row and "path" not in row for row in rows))

    def test_rejects_bad_ids_non_https_undated_stale_and_off_topic_rows(self):
        old_date = "2020-01-01T00:00:00+00:00"
        payload = {
            "candidates": {
                "tiktok": [
                    {**_row("../bad", "creator"), "title": "Poppy Playtime edit"},
                    {**_row("valid", "creator"), "url": "http://www.tiktok.com/@x/video/valid"},
                    {**_row("undated", "creator"), "published_at": None},
                    {**_row("stale", "creator"), "published_at": old_date},
                    {**_row("offtopic", "creator", title="Funny unrelated clip"), "caption": "nothing related", "hashtags": []},
                ]
            }
        }
        self.assertEqual(candidates_for_queue(payload), [])

    def test_round_robin_distributes_creators_and_rotates_after_previous_source(self):
        rows = []
        for creator in ("a", "b", "c"):
            rows.extend([
                {"external_id": creator + "1", "source_key": creator},
                {"external_id": creator + "2", "source_key": creator},
            ])
        self.assertEqual(
            [row["external_id"] for row in fair_round_robin(rows)[:4]],
            ["a1", "b1", "c1", "a2"],
        )
        self.assertEqual(
            [row["external_id"] for row in fair_round_robin(rows, last_source_key="b")[:3]],
            ["c1", "a1", "b1"],
        )

    def test_intake_skips_existing_ids_refills_duplicates_and_caps_four_new_items(self):
        candidates = []
        for creator_index in range(6):
            creator = f"creator{creator_index}"
            for video_index in range(4):
                external_id = _video_id(creator_index, video_index)
                candidates.append(_row(external_id, creator))
        candidates.insert(0, _row(_video_id(0, 99), "creator0"))
        candidates.append(_row(_video_id(5, 99), "creator5"))
        payload = {
            "schema_version": 1,
            "source": "termux_phone",
            "mode": "metadata_only_no_download_no_publish",
            "generated_at": _now_iso(),
            "candidates": {"tiktok": candidates, "mob_official": []},
        }

        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            report_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("agent.brain.queue_intake.CloudState", FakeCloudState):
                result = run_intake(report_path, max_new_items=4)

        self.assertTrue(result["ok"])
        self.assertEqual(result["inserted_count"], 4)
        self.assertEqual(result["max_new_items_per_run"], 4)
        self.assertEqual(len(result["selected_items"]), 4)
        self.assertEqual(len({row["source_key"] for row in result["selected_items"]}), 4)
        self.assertNotIn(
            _video_id(0, 99),
            {row["external_id"] for row in result["selected_items"]},
        )
        self.assertNotIn(
            _video_id(5, 99),
            {row["external_id"] for row in result["selected_items"]},
        )
        self.assertEqual(result["duplicate_count"], 1)
        self.assertTrue(all(row["queue_status"] == "queued" for row in result["selected_items"]))

    def test_rejects_bad_ids_and_non_https_urls(self):
        payload = {
            "candidates": {
                "tiktok": [
                    {**_row("../bad", "x"), "url": "https://www.tiktok.com/@x/video/123"},
                    {**_row("valid", "x"), "url": "http://www.tiktok.com/@x/video/123"},
                ]
            }
        }
        self.assertEqual(candidates_for_queue(payload), [])

    def test_search_query_and_creator_metadata_cannot_fake_relevance(self):
        unrelated = {
            **_row("unrelated", "smyxae", title="I think Carlos is my ideal type"),
            "caption": "Resident Evil edit",
            "hashtags": ["carlosoliveira", "residenteviledit"],
            "query": "poppyplaytime",
            "source_key": "Poppy Playtime search",
        }
        self.assertEqual(candidates_for_queue({"candidates": {"tiktok": [unrelated]}}), [])

    def test_rejects_game_mention_without_an_edit_signal(self):
        gameplay_news = {
            **_row("news-only", "creator", title="Poppy Playtime Chapter 6 is coming"),
            "caption": "Poppy Playtime Chapter 6 announcement",
            "hashtags": ["poppyplaytime", "chapter6"],
        }
        self.assertEqual(candidates_for_queue({"candidates": {"tiktok": [gameplay_news]}}), [])

    def test_accepts_relevant_character_edit_without_search_query(self):
        row = {
            **_row("huggy-edit", "creator", title="Huggy Wuggy edit"),
            "caption": "Fan edit",
            "hashtags": ["huggywuggy", "edit"],
        }
        result = candidates_for_queue({"candidates": {"tiktok": [row]}})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["external_id"], "huggy-edit")


    def test_hashtag_only_does_not_prove_relevance_and_filter_reasons_are_counted(self):
        hashtag_only = _row(
            "hashtagonly0001", "creator",
            title="#poppyplaytime #huggywuggy #edit",
        )
        hashtag_only["caption"] = "#poppyplaytime #huggywuggy #edit"
        hashtag_only["hashtags"] = ["poppyplaytime", "huggywuggy", "edit"]

        handle_only = _row("handleonly0001", "poppyplaytimeofficial", title="Funny unrelated clip")
        handle_only["caption"] = "Funny unrelated clip"
        handle_only["hashtags"] = ["edit"]

        generic_prototype = _row("prototype0001", "creator", title="Prototype edit")
        generic_prototype["caption"] = "Prototype edit"
        generic_prototype["hashtags"] = ["poppyplaytime"]

        no_edit_signal = _row("noedit000001", "creator", title="Huggy Wuggy news")
        no_edit_signal["caption"] = "Huggy Wuggy news"
        no_edit_signal["hashtags"] = []

        valid = _row("validhuggy001", "creator", title="Huggy Wuggy edit")
        valid["caption"] = "Huggy Wuggy edit"
        valid["hashtags"] = []

        duplicate = dict(valid)
        duplicate["author"] = "other_creator"

        diagnostics = {}
        rows = candidates_for_queue(
            {
                "candidates": {
                    "tiktok": [
                        hashtag_only,
                        handle_only,
                        generic_prototype,
                        no_edit_signal,
                        valid,
                        duplicate,
                    ]
                }
            },
            diagnostics=diagnostics,
        )

        self.assertEqual([row["external_id"] for row in rows], ["validhuggy001"])
        self.assertEqual(diagnostics["input_count"], 6)
        self.assertEqual(diagnostics["eligible_count"], 1)
        self.assertEqual(diagnostics["rejected_count"], 5)
        self.assertEqual(
            diagnostics["rejected_by_reason"]["poppy_relevance_not_verified_in_visible_text"],
            3,
        )
        self.assertEqual(diagnostics["rejected_by_reason"]["edit_signal_missing"], 1)
        self.assertEqual(diagnostics["duplicate_within_report_count"], 1)

    def test_urls_hashtags_and_creator_handles_cannot_fake_poppy_relevance(self):
        hashtag_only = {"title": "Unrelated clip", "caption": "#smilingcritters #poppyplaytime"}
        url_only = {"title": "https://www.tiktok.com/tag/poppyplaytime", "caption": "Funny unrelated clip"}
        handle_only = {"title": "@smilingcritters", "caption": "Funny unrelated clip"}
        valid_text = {"title": "Huggy Wuggy edit", "caption": "#smilingcritters"}

        self.assertEqual(_visible_metadata_text(hashtag_only), "unrelated clip")
        self.assertEqual(_visible_metadata_text(url_only), "funny unrelated clip")
        self.assertEqual(_visible_metadata_text(handle_only), "funny unrelated clip")
        self.assertFalse(_is_poppy_related(hashtag_only))
        self.assertFalse(_is_poppy_related(url_only))
        self.assertFalse(_is_poppy_related(handle_only))
        self.assertTrue(_is_poppy_related(valid_text))

if __name__ == "__main__":
    unittest.main()
