from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent.brain import mob_official_trial
from agent.brain.mob_official_trial import load_x_candidates_from_report, preview_freshness_rejection, run_trial


class MobOfficialTrialReportTests(unittest.TestCase):
    def row(self, **changes):
        row = {
            "platform": "mob_official",
            "id": "x-1734258130186559716",
            "external_id": "x-1734258130186559716",
            "url": "https://x.com/mobgamesstudios/status/1734258130186559716",
            "canonical_url": "https://x.com/mobgamesstudios/status/1734258130186559716",
            "title": "Official post",
            "caption": "Original Mob caption",
            "original_caption": "Original Mob caption",
            "author": "@mobgamesstudios",
            "source_key": "x.com/mobgamesstudios",
            "source_name": "Mob Entertainment on X",
            "source_platform": "x",
            "official_source": True,
            "published_at": "2026-10-14T12:00:00+00:00",
            "media_type": "image",
            "media_url": "https://pbs.twimg.com/media/photo.jpg",
        }
        row.update(changes)
        return row

    def report_file(self, folder: str, rows):
        path = Path(folder) / "termux_discovery.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "source": "termux_phone",
            "mode": "metadata_only_no_download_no_publish",
            "candidates": {
                "tiktok": [],
                "mob_official": rows,
            },
        }), encoding="utf-8")
        return path

    def test_accepts_only_verified_official_x_post_with_real_media_host(self):
        valid = self.row()
        wrong_handle = self.row(author="@fanaccount", id="x-1734258130186559717", external_id="x-1734258130186559717")
        wrong_source = self.row(source_platform="youtube", id="x-1734258130186559718", external_id="x-1734258130186559718")
        thumbnail_only = self.row(media_url="https://pbs.twimg.com/media/video-thumbnail.jpg", media_type="video",
                                  id="x-1734258130186559719", external_id="x-1734258130186559719")
        with tempfile.TemporaryDirectory() as folder:
            path = self.report_file(folder, [valid, wrong_handle, wrong_source, thumbnail_only])
            candidates, status = load_x_candidates_from_report(path)

        self.assertEqual(status, "ok")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["external_id"], "x-1734258130186559716")

    def test_rejects_invalid_report_schema_and_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing.json"
            self.assertEqual(load_x_candidates_from_report(missing), ([], "termux_report_unreadable"))
            invalid = Path(folder) / "invalid.json"
            invalid.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
            self.assertEqual(load_x_candidates_from_report(invalid), ([], "termux_report_invalid"))


    def test_accepts_verified_official_website_youtube_and_steam_media(self):
        website = self.row(
            id="pop-chapter-five",
            external_id="pop-chapter-five",
            url="https://www.mobentertainment.com/news/chapter-five",
            canonical_url="https://www.mobentertainment.com/news/chapter-five",
            title="Official chapter news",
            author="",
            source_platform="website",
            source_name="Mob Entertainment",
            source_key="mobentertainment.com",
            media_type="image",
            media_url="https://cdn.mobentertainment.com/news/chapter-five.jpg",
            published_at="2026-10-14T12:00:00+00:00",
        )
        youtube = self.row(
            id="yt-abcdefghijk",
            external_id="yt-abcdefghijk",
            url="https://www.youtube.com/watch?v=abcdefghijk",
            canonical_url="https://www.youtube.com/watch?v=abcdefghijk",
            source_platform="youtube",
            source_name="Mob Entertainment YouTube",
            source_key="youtube.com",
            author="",
            media_type="video",
            media_url="https://www.youtube.com/watch?v=abcdefghijk",
        )
        steam = self.row(
            id="steam-12345678901",
            external_id="steam-12345678901",
            url="https://steamcommunity.com/games/1721470/announcements/detail/12345678901",
            canonical_url="https://steamcommunity.com/games/1721470/announcements/detail/12345678901",
            source_platform="steam",
            source_name="Poppy Playtime official Steam news",
            source_key="steamcommunity.com",
            author="",
            media_type="image",
            media_url="https://clan.cloudflare.steamstatic.com/images/12345/update.jpg",
        )
        with tempfile.TemporaryDirectory() as folder:
            path = self.report_file(folder, [website, youtube, steam])
            candidates, status = load_x_candidates_from_report(path)

        self.assertEqual(status, "ok")
        self.assertEqual([row["source_platform"] for row in candidates], ["website", "youtube", "steam"])

    def test_rejects_official_page_with_untrusted_media_host(self):
        forged_media = self.row(
            id="official-story",
            external_id="official-story",
            url="https://www.mobentertainment.com/news/story",
            canonical_url="https://www.mobentertainment.com/news/story",
            source_platform="website",
            source_name="Mob Entertainment",
            media_type="image",
            media_url="https://evil.example/tracker.jpg",
        )
        with tempfile.TemporaryDirectory() as folder:
            path = self.report_file(folder, [forged_media])
            candidates, status = load_x_candidates_from_report(path)

        self.assertEqual(candidates, [])
        self.assertEqual(status, "no_verified_official_posts_in_termux_report")


class OfficialPreviewSafetyTests(unittest.TestCase):
    def test_preview_requires_a_verified_recent_timestamp(self):
        now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
        recent = {"published_at": "2026-09-20T16:44:36+00:00"}
        old = {"published_at": "2024-02-26T20:00:53+00:00"}
        undated = {"published_at": None}
        future = {"published_at": "2026-10-18T12:00:00+00:00"}

        self.assertIsNone(preview_freshness_rejection(recent, now=now))
        self.assertEqual(
            preview_freshness_rejection(old, now=now),
            "preview_candidate_older_than_90_days",
        )
        self.assertEqual(
            preview_freshness_rejection(undated, now=now),
            "publication_time_unverified",
        )
        self.assertEqual(
            preview_freshness_rejection(future, now=now),
            "publication_time_in_future",
        )

    def test_publish_mode_is_blocked_while_lifecycle_is_test(self):
        class FakeCloud:
            def __init__(self, *args, **kwargs):
                pass

            def get_seen_index(self):
                return {"ids": set(), "hashes": set()}

        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "report.json"
            with patch.object(mob_official_trial, "CloudState", FakeCloud):
                result = run_trial(
                    publish=True,
                    limit=1,
                    output_report=report,
                    output_dir=Path(folder) / "media",
                    candidate_report=Path(folder) / "missing-candidates.json",
                )

        self.assertEqual(result["error"], "publishing_requires_production_lifecycle")
        self.assertFalse(result["publishing"])
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["error"],
                         "publishing_requires_production_lifecycle")

    def test_publish_mode_also_requires_explicit_source_config_approval(self):
        class FakeCloud:
            def __init__(self, *args, **kwargs):
                pass

            def get_seen_index(self):
                return {"ids": set(), "hashes": set()}

        production_policy = {
            "mode": "production",
            "born_at": "2026-10-14T12:00:00Z",
            "birth_datetime": datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc),
        }
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "report.json"
            with (
                patch.object(mob_official_trial, "CloudState", FakeCloud),
                patch.object(mob_official_trial, "load_lifecycle_policy", return_value=production_policy),
                patch.object(mob_official_trial, "_official_publishing_config_enabled", return_value=False),
            ):
                result = run_trial(
                    publish=True,
                    limit=1,
                    output_report=report,
                    output_dir=Path(folder) / "media",
                    candidate_report=Path(folder) / "missing-candidates.json",
                )

        self.assertEqual(result["error"], "official_publishing_config_disabled")
        self.assertFalse(result["publishing"])


if __name__ == "__main__":
    unittest.main()
