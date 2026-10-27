from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent.brain import mob_official_trial, test_channel
from agent.brain.mob_official_trial import (
    filter_candidates_by_since,
    filter_official_candidates_by_source,
    load_official_candidates_from_report,
    load_x_candidates_from_report,
    preview_freshness_rejection,
    publication_window_rejection,
    run_trial,
    since_rule_rejection,
    sort_candidates_oldest_first,
)


TEST_CHANNEL_FOR_TESTS = "synthetic-test-channel-guid"


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
            "published_at": "2026-10-20T12:00:00+00:00",
            "freshness_verified": True,
            "media_type": "image",
            "media_url": "https://pbs.twimg.com/media/photo.jpg",
        }
        row.update(changes)
        return row

    def report_file(self, folder: str, rows, source="termux_phone"):
        path = Path(folder) / "termux_discovery.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "source": source,
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

    def test_accepts_phone_and_github_actions_termux_docker_report_sources(self):
        for source in ("termux_phone", "github_actions_termux_docker"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as folder:
                path = self.report_file(folder, [self.row()], source=source)
                candidates, status = load_official_candidates_from_report(path)
            self.assertEqual(status, "ok")
            self.assertEqual(len(candidates), 1)

    def test_rejects_invalid_report_schema_and_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing.json"
            self.assertEqual(load_x_candidates_from_report(missing), ([], "termux_report_unreadable"))
            invalid = Path(folder) / "invalid.json"
            invalid.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
            self.assertEqual(load_x_candidates_from_report(invalid), ([], "termux_report_invalid"))


    def test_rejects_youtube_and_instagram_source_urls_but_keeps_website_and_steam(self):
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
        instagram = self.row(
            id="ig-AbCdEf12345",
            external_id="ig-AbCdEf12345",
            url="https://www.instagram.com/reel/AbCdEf12345/",
            canonical_url="https://www.instagram.com/reel/AbCdEf12345/",
            source_platform="instagram",
            source_name="Poppy Playtime Instagram",
            source_key="instagram.com",
            author="",
            media_type="video",
            media_url="https://www.instagram.com/reel/AbCdEf12345/",
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
            path = self.report_file(folder, [website, youtube, instagram, steam])
            candidates, status = load_x_candidates_from_report(path)

        self.assertEqual(status, "ok")
        self.assertEqual([row["source_platform"] for row in candidates], ["website", "steam"])
        self.assertNotIn("youtube", mob_official_trial.OFFICIAL_SOURCE_PLATFORMS)
        with self.assertRaisesRegex(ValueError, "invalid_official_source_platform"):
            filter_official_candidates_by_source([], "youtube")

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


    def test_source_lane_filter_never_backfills_from_another_official_source(self):
        candidates = [
            {"source_platform": "x", "external_id": "x-1"},
            {"source_platform": "youtube", "external_id": "yt-1"},
            {"source_platform": "website", "external_id": "site-1"},
            {"source_platform": "steam", "external_id": "steam-1"},
        ]

        selected = filter_official_candidates_by_source(candidates, "x")

        self.assertEqual(selected, [{"source_platform": "x", "external_id": "x-1"}])
        self.assertEqual(len(candidates), 4)
        self.assertEqual(filter_official_candidates_by_source(candidates), candidates)
        self.assertEqual(filter_official_candidates_by_source(candidates, "youtube"), [candidates[1]])

    def test_source_lane_filter_rejects_unknown_lane(self):
        with self.assertRaisesRegex(ValueError, "invalid_official_source_platform"):
            filter_official_candidates_by_source([{"source_platform": "x"}], "random")


class OfficialXPublicationWindowTests(unittest.TestCase):
    def test_only_verified_x_posts_inside_the_five_hour_window_pass(self):
        now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
        recent = {"source_platform": "x", "freshness_verified": True, "published_at": "2026-10-16T11:59:00Z"}
        boundary = {"source_platform": "x", "freshness_verified": True, "published_at": "2026-10-16T07:00:00Z"}
        stale = {"source_platform": "x", "freshness_verified": True, "published_at": "2026-10-16T06:59:59Z"}
        future = {"source_platform": "x", "freshness_verified": True, "published_at": "2026-10-16T12:06:00Z"}
        unverified = {"source_platform": "x", "freshness_verified": False, "published_at": "2026-10-16T11:59:00Z"}
        other_lane = {"source_platform": "youtube", "freshness_verified": True, "published_at": "2026-10-16T11:59:00Z"}

        self.assertIsNone(publication_window_rejection(recent, now=now, window_hours=5))
        self.assertIsNone(publication_window_rejection(boundary, now=now, window_hours=5))
        self.assertEqual(publication_window_rejection(stale, now=now, window_hours=5), "outside_recent_publication_window")
        self.assertEqual(publication_window_rejection(future, now=now, window_hours=5), "publication_time_in_future")
        self.assertEqual(publication_window_rejection(unverified, now=now, window_hours=5), "publication_time_unverified")
        self.assertEqual(publication_window_rejection(other_lane, now=now, window_hours=5), "official_x_source_required")


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

        test_policy = {"mode": "test", "born_at": None, "birth_datetime": None}
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "report.json"
            with (
                patch.object(mob_official_trial, "CloudState", FakeCloud),
                patch.object(mob_official_trial, "load_lifecycle_policy", return_value=test_policy),
            ):
                result = run_trial(
                    publish=True,
                    limit=1,
                    output_report=report,
                    output_dir=Path(folder) / "media",
                    candidate_report=Path(folder) / "missing-candidates.json",
                )

        self.assertEqual(result["error"], "publishing_requires_production_lifecycle")
        self.assertFalse(result["publishing"])

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


class TestChannelPublishingGateTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(
            test_channel, "is_test_channel_guid",
            side_effect=lambda value: str(value or "").strip() == TEST_CHANNEL_FOR_TESTS,
        )
        guard.start()
        self.addCleanup(guard.stop)
    def test_config_allows_production_x_publishing_and_the_fixed_test_channel_trial(self):
        self.assertTrue(mob_official_trial._official_publishing_config_enabled())
        self.assertTrue(mob_official_trial._test_channel_publishing_config_enabled())

    def test_test_channel_trial_in_production_still_applies_mob_birth_cutoff(self):
        class FakeCloud:
            def __init__(self, *args, **kwargs):
                pass

            def get_seen_index(self):
                return {"ids": set(), "hashes": set()}

        production_policy = {
            "mode": "production",
            "born_at": "2026-10-20T00:00:00Z",
            "birth_datetime": datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc),
        }
        with tempfile.TemporaryDirectory() as folder:
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
                "published_at": "2026-10-19T23:59:59Z",
                "freshness_verified": True,
                "media_type": "image",
                "media_url": "https://pbs.twimg.com/media/photo.jpg",
            }
            candidates = Path(folder) / "termux_discovery.json"
            candidates.write_text(json.dumps({
                "schema_version": 1,
                "source": "termux_phone",
                "mode": "metadata_only_no_download_no_publish",
                "candidates": {"tiktok": [], "mob_official": [row]},
            }), encoding="utf-8")
            report_path = Path(folder) / "report.json"
            with (
                patch.object(mob_official_trial, "CloudState", FakeCloud),
                patch.object(mob_official_trial, "load_lifecycle_policy", return_value=production_policy),
                patch.dict("os.environ", {
                    "RUBIKA_CHANNEL_GUID": TEST_CHANNEL_FOR_TESTS,
                    "RUBIKA_TOKEN": "dummy-token",
                }),
            ):
                result = run_trial(
                    publish=False,
                    test_channel_publish=True,
                    limit=1,
                    output_report=report_path,
                    output_dir=Path(folder) / "media",
                    candidate_report=candidates,
                )

        self.assertEqual(result["status"], "nothing_new")
        self.assertEqual(result["lifecycle"]["mode"], "production")
        self.assertTrue(result["lifecycle"]["birth_cutoff_enabled"])
        self.assertEqual(result["lifecycle"]["rejected_before_or_unverified"], 1)
        self.assertEqual(result["candidate_count"], 0)
        self.assertFalse(result["publishing"])

    def test_production_publish_refuses_any_destination_other_than_fixed_test_channel(self):
        class FakeCloud:
            def __init__(self, *args, **kwargs):
                pass

            def get_seen_index(self):
                return {"ids": set(), "hashes": set()}

        production_policy = {
            "mode": "production",
            "born_at": "2026-10-20T00:00:00Z",
            "birth_datetime": datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc),
        }
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(mob_official_trial, "CloudState", FakeCloud),
                patch.object(mob_official_trial, "load_lifecycle_policy", return_value=production_policy),
                patch.dict("os.environ", {
                    "RUBIKA_CHANNEL_GUID": "not-the-test-channel",
                    "RUBIKA_TOKEN": "dummy-token",
                }),
            ):
                result = run_trial(
                    publish=True,
                    limit=1,
                    output_report=Path(folder) / "report.json",
                    output_dir=Path(folder) / "media",
                    candidate_report=Path(folder) / "missing-candidates.json",
                )

        self.assertEqual(result["error"], "official_publishing_target_mismatch")
        self.assertFalse(result["publishing"])

    def test_test_channel_publish_refuses_a_different_destination(self):
        class FakeCloud:
            def __init__(self, *args, **kwargs):
                pass

            def get_seen_index(self):
                return {"ids": set(), "hashes": set()}

        test_policy = {"mode": "test", "born_at": None, "birth_datetime": None}
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(mob_official_trial, "CloudState", FakeCloud),
                patch.object(mob_official_trial, "load_lifecycle_policy", return_value=test_policy),
                patch.object(mob_official_trial, "_test_channel_publishing_config_enabled", return_value=True),
                patch.dict("os.environ", {"RUBIKA_CHANNEL_GUID": "not-the-test-channel", "RUBIKA_TOKEN": "dummy-token"}),
            ):
                result = run_trial(
                    publish=False,
                    test_channel_publish=True,
                    limit=1,
                    output_report=Path(folder) / "report.json",
                    output_dir=Path(folder) / "media",
                    candidate_report=Path(folder) / "missing-candidates.json",
                )

        self.assertEqual(result["error"], "test_channel_target_mismatch")
        self.assertFalse(result["publishing"])

    def test_test_channel_publish_requires_explicit_config_and_token(self):
        class FakeCloud:
            def __init__(self, *args, **kwargs):
                pass

            def get_seen_index(self):
                return {"ids": set(), "hashes": set()}

        test_policy = {"mode": "test", "born_at": None, "birth_datetime": None}
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(mob_official_trial, "CloudState", FakeCloud),
                patch.object(mob_official_trial, "load_lifecycle_policy", return_value=test_policy),
                patch.object(mob_official_trial, "_test_channel_publishing_config_enabled", return_value=False),
                patch.dict("os.environ", {
                    "RUBIKA_CHANNEL_GUID": TEST_CHANNEL_FOR_TESTS,
                    "RUBIKA_TOKEN": "dummy-token",
                }),
            ):
                result = run_trial(
                    publish=False,
                    test_channel_publish=True,
                    limit=1,
                    output_report=Path(folder) / "report.json",
                    output_dir=Path(folder) / "media",
                    candidate_report=Path(folder) / "missing-candidates.json",
                )

        self.assertEqual(result["error"], "test_channel_publishing_config_disabled")
        self.assertFalse(result["publishing"])


class OfficialSinceRuleTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(
            test_channel, "is_test_channel_guid",
            side_effect=lambda value: str(value or "").strip() == TEST_CHANNEL_FOR_TESTS,
        )
        guard.start()
        self.addCleanup(guard.stop)
    def row(self, external_id, published_at, **changes):
        row = {
            "platform": "mob_official",
            "external_id": external_id,
            "id": external_id,
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
            "freshness_verified": True,
            "published_at": published_at,
            "media_type": "image",
            "media_url": "https://pbs.twimg.com/media/photo.jpg",
        }
        row.update(changes)
        return row

    def test_since_cutoff_rejects_old_accepts_new_and_rejects_unverified_time(self):
        cutoff = "2026-10-20T00:00:00Z"
        now = datetime(2026, 10, 24, tzinfo=timezone.utc)
        old = self.row("old", "2026-10-19T23:59:59Z")
        boundary = self.row("boundary", cutoff)
        newer = self.row("newer", "2026-10-21T00:00:00Z")
        unverified = self.row("unverified", "2026-10-22T00:00:00Z", freshness_verified=False)

        self.assertEqual(since_rule_rejection(old, cutoff, now=now), "before_since_utc")
        self.assertIsNone(since_rule_rejection(boundary, cutoff, now=now))
        self.assertIsNone(since_rule_rejection(newer, cutoff, now=now))
        self.assertEqual(since_rule_rejection(unverified, cutoff, now=now), "publication_time_unverified")
        accepted, rejected = filter_candidates_by_since([newer, old, boundary, unverified], cutoff)
        self.assertEqual([row["external_id"] for row in accepted], ["newer", "boundary"])
        self.assertEqual([row["reason"] for row in rejected], ["before_since_utc", "publication_time_unverified"])

    def test_candidates_are_sorted_oldest_first_stably(self):
        candidates = [
            self.row("newest", "2026-10-23T00:00:00Z"),
            self.row("oldest", "2026-10-20T00:00:01Z"),
            self.row("middle", "2026-10-21T00:00:00Z"),
        ]
        self.assertEqual(
            [row["external_id"] for row in sort_candidates_oldest_first(candidates)],
            ["oldest", "middle", "newest"],
        )

    def test_publish_run_sends_one_oldest_unseen_post_and_preserves_deduplication(self):
        class FakeCloud:
            def __init__(self, *args, **kwargs):
                self.upserted = []
                self.attempts = []

            def get_seen_index(self):
                return {"ids": {"mob_official:x-seen", "x-seen"}, "hashes": {"sha-known"}}

            def upsert_item(self, item):
                self.upserted.append(item)

            def record_attempt(self, item_id, attempt_number, status, **kwargs):
                self.attempts.append((item_id, status, kwargs))

        class FakePublisher:
            def __init__(self):
                self.calls = []

            def send_media(self, media_path, media_type, caption):
                self.calls.append((str(media_path), media_type, caption))
                return {"status": "OK", "data": {"message_id": "message-1"}}

        cutoff = "2026-10-20T00:00:00Z"
        policy = {
            "mode": "production",
            "born_at": cutoff,
            "birth_datetime": datetime(2026, 10, 20, tzinfo=timezone.utc),
        }
        candidates = [
            self.row("x-newer", "2026-10-23T00:00:00Z"),
            self.row("x-oldhash", "2026-10-20T00:30:00Z"),
            self.row("x-seen", "2026-10-20T01:00:00Z"),
            self.row("x-oldest-valid", "2026-10-20T02:00:00Z"),
        ]
        publisher = FakePublisher()
        media_hash = {
            "x-oldhash": "sha-known",
            "x-oldest-valid": "sha-new",
            "x-newer": "sha-newer",
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_media = {}
            for candidate in candidates:
                if candidate["external_id"] == "x-seen":
                    continue
                media_path = root / (candidate["external_id"] + ".jpg")
                media_path.write_bytes(b"fake image bytes")
                source_media[candidate["external_id"]] = media_path

            def fake_download(item, output_dir):
                return source_media[item["external_id"]], {
                    "sha256": media_hash[item["external_id"]],
                    "media_type": "image",
                    "size_bytes": 16,
                    "mime_type": "image/jpeg",
                }

            with (
                patch.object(mob_official_trial, "CloudState", FakeCloud),
                patch.object(mob_official_trial, "load_lifecycle_policy", return_value=policy),
                patch.object(mob_official_trial, "_official_publishing_config_enabled", return_value=True),
                patch.object(mob_official_trial, "discover_official_x_posts", return_value=(candidates, [])),
                patch.object(mob_official_trial, "format_mob_post", side_effect=lambda value: {
                    "original_caption": value.get("original_caption") or "Original caption",
                    "persian_translation": "ترجمه تأییدشده",
                    "translation_status": "translated",
                    "caption": "ترجمه تأییدشده",
                }),
                patch.object(mob_official_trial, "download_and_validate", side_effect=fake_download),
                patch.object(mob_official_trial, "RubikaPublisher", return_value=publisher),
                patch.dict("os.environ", {
                    "RUBIKA_CHANNEL_GUID": TEST_CHANNEL_FOR_TESTS,
                    "RUBIKA_TOKEN": "dummy-token",
                }),
            ):
                result = run_trial(
                    publish=True,
                    limit=1,
                    output_report=root / "report.json",
                    output_dir=root / "media",
                    source_platform="x",
                )

        self.assertEqual(result["status"], "published")
        self.assertEqual(result["selected"]["external_id"], "x-oldest-valid")
        self.assertEqual(len(publisher.calls), 1)
        self.assertEqual(result["backlog_count"], 1)
        evaluations = {row.get("external_id"): row.get("result") for row in result["candidate_evaluations"]}
        self.assertEqual(evaluations["x-oldhash"], "duplicate_media_hash")
        self.assertNotIn("x-seen", evaluations)

if __name__ == "__main__":
    unittest.main()
