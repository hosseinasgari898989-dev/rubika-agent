from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain import queue_worker
from agent.brain.cloud_state import CloudStateError


def make_item(platform: str, external_id: str, title: str) -> dict:
    return {
        "id": f"{platform}:{external_id}",
        "platform": platform,
        "external_id": external_id,
        "canonical_url": f"https://www.tiktok.com/@creator/video/{external_id}",
        "source_key": "@creator",
        "title": title,
        "caption": "Poppy Playtime edit",
        "published_at": None,
        "media_type": "video",
        "freshness_verified": 1,
        "method": "unit_test",
        "metadata_json": "{}",
        "status": "queued",
        "attempt_count": 0,
    }


class FakeCloud:
    def __init__(self, *args, **kwargs):
        self.items = [
            make_item("tiktok", "7616247520657771796", "First failed candidate"),
            make_item("tiktok", "7616247520657771797", "Success 1"),
            make_item("tiktok", "7616247520657771798", "Success 2"),
            make_item("tiktok", "7616247520657771799", "Success 3"),
            make_item("tiktok", "7616247520657771800", "Success 4"),
            make_item("tiktok", "7616247520657771801", "Remain queued"),
        ]

    def get_automation_queue(self, *, status="queued", limit=100, offset=0):
        rows = [dict(item) for item in self.items if item["status"] == status]
        return {"ok": True, "items": rows[offset:offset + limit]}

    def update_automation_queue_item(self, item_id, status, *, error_code=None):
        item = next(item for item in self.items if item["id"] == item_id)
        item["status"] = status
        item["last_error_code"] = error_code
        return {"ok": True, "id": item_id, "status": status}

    def get_automation_queue_summary(self):
        result = {}
        for item in self.items:
            result[item["status"]] = result.get(item["status"], 0) + 1
        for key in ("queued", "processing", "sent", "failed", "uncertain", "skipped"):
            result.setdefault(key, 0)
        result["sent_today"] = result["sent"]
        result["usage_day"] = "2026-10-14"
        return result


class QueueWorkerTests(unittest.TestCase):
    def test_processes_more_than_four_successes_across_queue_pages(self):
        cloud = FakeCloud()
        cloud.items = [
            make_item("tiktok", str(7700000000000000000 + index), f"Success {index}")
            for index in range(107)
        ]

        def fake_run_one(report_path, platform, output_report, **kwargs):
            payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
            row = payload["candidates"][platform][0]
            return {
                "ok": True, "status": "sent",
                "platform": platform, "external_id": row["id"],
                "title": row["title"], "publishing": True,
            }

        with (
            patch.object(queue_worker, "CloudState", return_value=cloud),
            patch.object(queue_worker, "run_one", side_effect=fake_run_one),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
        ):
            report = queue_worker.run_daily(send_delay_seconds=0)

        self.assertEqual(report["sent_count"], 107)
        self.assertEqual(report["attempted_count"], 107)
        self.assertEqual(report["queue_counts"]["queued"], 0)
        self.assertEqual(report["queue_counts"]["sent"], 107)
        self.assertEqual(report["status"], "sent")

    def test_report_keeps_music_diagnostics_for_pass_ambiguous_no_audio_and_error(self):
        outcomes = [
            {
                "ok": True, "status": "sent", "music_probability": 0.91,
                "has_audio_stream": True,
            },
            {
                "ok": False, "status": "staging_failed",
                "reason": "music_probability_ambiguous",
                "music_probability": 0.42, "has_audio_stream": True,
            },
            {
                "ok": False, "status": "staging_failed", "reason": "no_audio",
                "music_probability": None, "has_audio_stream": False,
            },
            RuntimeError("mocked pipeline error"),
        ]
        calls = {"count": 0}
        def fake_run_one(*_args, **_kwargs):
            index = calls["count"]
            calls["count"] += 1
            outcome = outcomes[index]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with (
            patch.object(queue_worker, "CloudState", FakeCloud),
            patch.object(queue_worker, "run_one", side_effect=fake_run_one),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
        ):
            report = queue_worker.run_daily(send_delay_seconds=0)

        self.assertEqual(calls["count"], 4)
        self.assertEqual(len(report["results"]), 4)
        passed, ambiguous, no_audio, errored = report["results"]
        for row in report["results"]:
            self.assertIn("music_probability", row)
            self.assertIn("has_audio_stream", row)
        self.assertEqual(passed["music_probability"], 0.91)
        self.assertIs(type(passed["music_probability"]), float)
        self.assertIs(passed["has_audio_stream"], True)
        self.assertEqual(ambiguous["music_probability"], 0.42)
        self.assertIs(type(ambiguous["music_probability"]), float)
        self.assertIs(ambiguous["has_audio_stream"], True)
        self.assertIsNone(no_audio["music_probability"])
        self.assertIs(no_audio["has_audio_stream"], False)
        self.assertIsNone(errored["music_probability"])
        self.assertIsNone(errored["has_audio_stream"])

    def test_failure_report_preserves_sanitized_staging_diagnostics(self):
        def fake_run_one(*args, **kwargs):
            return {
                "ok": False,
                "status": "staging_failed",
                "reason": "cloud_sync_failed",
                "failure_details": [
                    {"item": "tiktok:12345", "reason": "download_timeout"},
                    {"item": "bad/path?token=hidden", "reason": "media_validation_failed"},
                ],
                "cloud_sync_errors": [
                    "tiktok:12345:control_api_http_500_item_upsert_failed",
                    "provider response contained https://private.invalid/?token=hidden",
                ],
                "stage": {"found_count": 1, "authorized_count": 1, "staged_count": 0},
                "provider_body": "secret token must not appear",
            }

        with (
            patch.object(queue_worker, "CloudState", FakeCloud),
            patch.object(queue_worker, "run_one", side_effect=fake_run_one),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
        ):
            report = queue_worker.run_daily(send_delay_seconds=0)

        result = report["results"][0]
        self.assertEqual(result["reason"], "cloud_sync_failed")
        self.assertEqual(result["failure_details"][0]["reason"], "download_timeout")
        self.assertEqual(result["failure_details"][1]["item"], "unknown")
        self.assertEqual(result["cloud_sync_errors"][0], "tiktok:12345:control_api_http_500_item_upsert_failed")
        self.assertEqual(result["cloud_sync_errors"][1], "cloud_sync_error")
        self.assertEqual(result["found_count"], 1)
        self.assertEqual(result["authorized_count"], 1)
        self.assertEqual(result["staged_count"], 0)
        self.assertNotIn("provider_body", result)
        self.assertNotIn("private.invalid", json.dumps(result))
        self.assertNotIn("hidden", json.dumps(result))

    def test_cloud_state_http_error_details_reach_queue_report_without_secrets(self):
        def fail_with_cloud_error(*args, **kwargs):
            raise CloudStateError(
                "control_api_http_400_edit_approval_requires_visual_content_and_music_checks",
                status_code=400,
                endpoint_path="/api/brain/items/upsert?token=hidden&channel=private",
            )

        with (
            patch.object(queue_worker, "CloudState", FakeCloud),
            patch.object(queue_worker, "run_one", side_effect=fail_with_cloud_error),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
        ):
            report = queue_worker.run_daily(send_delay_seconds=0)

        result = report["results"][0]
        self.assertEqual(
            result["reason"],
            "control_api_http_400_edit_approval_requires_visual_content_and_music_checks",
        )
        self.assertEqual(result["cloud_state_error"], {
            "status_code": 400,
            "endpoint_path": "/api/brain/items/upsert",
            "code": "control_api_http_400_edit_approval_requires_visual_content_and_music_checks",
        })
        serialized = json.dumps(report)
        self.assertNotIn("hidden", serialized)
        self.assertNotIn("private", serialized)
        self.assertNotIn("token", serialized)
        self.assertNotIn("response_body", serialized)

    def test_existing_daily_sent_count_does_not_block_more_tiktok_sends(self):
        cloud = FakeCloud()
        for item in cloud.items[:4]:
            item["status"] = "sent"

        def fake_run_one(report_path, platform, output_report, **kwargs):
            payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
            row = payload["candidates"][platform][0]
            return {"ok": True, "status": "sent", "title": row["title"]}

        with (
            patch.object(queue_worker, "CloudState", return_value=cloud),
            patch.object(queue_worker, "run_one", side_effect=fake_run_one) as mocked_run,
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
        ):
            report = queue_worker.run_daily(send_delay_seconds=0)

        self.assertEqual(mocked_run.call_count, 2)
        self.assertEqual(report["daily_sent_before"], 4)
        self.assertEqual(report["sent_count"], 2)
        self.assertEqual(report["queue_counts"]["sent_today"], 6)

    def test_time_budget_stops_between_items_and_leaves_rest_queued(self):
        cloud = FakeCloud()
        elapsed = [0.0]

        def fake_run_one(report_path, platform, output_report, **kwargs):
            elapsed[0] += 2.0
            return {"ok": True, "status": "sent", "title": "First"}

        with (
            patch.object(queue_worker, "CloudState", return_value=cloud),
            patch.object(queue_worker, "run_one", side_effect=fake_run_one),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
        ):
            report = queue_worker.run_daily(
                time_budget_seconds=1, send_delay_seconds=0,
                clock=lambda: elapsed[0], sleeper=lambda _seconds: None,
            )

        self.assertEqual(report["sent_count"], 1)
        self.assertEqual(report["status"], "time_budget_exhausted")
        self.assertTrue(report["ok"])
        self.assertEqual(cloud.items[0]["status"], "sent")
        self.assertEqual(cloud.items[1]["status"], "queued")
        self.assertEqual(cloud.items[2]["status"], "queued")

    def test_configurable_delay_is_applied_between_successful_sends(self):
        cloud = FakeCloud()
        cloud.items = cloud.items[:2]
        sleeps = []

        def fake_run_one(report_path, platform, output_report, **kwargs):
            return {"ok": True, "status": "sent", "title": "Sent"}

        with (
            patch.object(queue_worker, "CloudState", return_value=cloud),
            patch.object(queue_worker, "run_one", side_effect=fake_run_one),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
        ):
            report = queue_worker.run_daily(
                send_delay_seconds=4,
                sleeper=lambda seconds: sleeps.append(seconds),
            )

        self.assertEqual(report["sent_count"], 2)
        self.assertEqual(sleeps, [4])

    def test_rate_limit_requeues_current_item_and_stops_remaining_work(self):
        cloud = FakeCloud()
        calls = []

        def fake_run_one(*_args, **_kwargs):
            calls.append(True)
            return {
                "ok": False, "status": "rate_limited",
                "reason": "rubika_rate_limited",
            }

        with (
            patch.object(queue_worker, "CloudState", return_value=cloud),
            patch.object(queue_worker, "run_one", side_effect=fake_run_one),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
        ):
            report = queue_worker.run_daily(send_delay_seconds=0)

        self.assertEqual(len(calls), 1)
        self.assertEqual(report["status"], "rate_limited")
        self.assertTrue(report["ok"])
        self.assertEqual(cloud.items[0]["status"], "queued")
        self.assertEqual(cloud.items[1]["status"], "queued")
        self.assertEqual(report["attempted_count"], 1)

    def test_worker_main_sends_unlimited_queue_notification_without_name_error(self):
        messages = []
        report = {
            "ok": True,
            "status": "sent",
            "sent_count": 1,
            "failed_count": 0,
            "skipped_count": 0,
            "attempted_count": 1,
            "queue_counts": {"queued": 3, "processing": 0, "sent": 1, "failed": 0,
                             "uncertain": 0, "skipped": 0, "sent_today": 1},
            "inventory_text": "📦 وضعیت صف: در انتظار 3",
            "errors": [],
        }
        with tempfile.TemporaryDirectory() as folder:
            report_path = Path(folder) / "report.json"
            with (
                patch("sys.argv", ["queue_worker", "--time-budget-seconds", "9000", "--send-delay-seconds", "0", "--report", str(report_path)]),
                patch.object(queue_worker, "run_daily", return_value=report),
                patch.object(queue_worker, "send_owner_notification",
                             side_effect=lambda message: (messages.append(message) or {"sent": True})),
            ):
                exit_code = queue_worker.main()

            saved = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(messages), 1)
        self.assertIn("بدون سقف روزانه", messages[0])
        self.assertTrue(saved["final_notification_sent"])

    def test_unsatisfied_delivery_goal_is_not_reported_as_ok(self):
        def fail_staging(*args, **kwargs):
            return {"ok": False, "status": "staging_failed", "reason": "download_failed"}

        with (
            patch.object(queue_worker, "CloudState", FakeCloud),
            patch.object(queue_worker, "run_one", side_effect=fail_staging),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
        ):
            report = queue_worker.run_daily(send_delay_seconds=0)

        self.assertEqual(report["sent_count"], 0)
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["ok"])

    def test_uncertain_delivery_stops_run_before_attempting_another_video(self):
        cloud = FakeCloud()
        calls = []

        def fake_run_one(report_path, platform, output_report, **kwargs):
            payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
            row = payload["candidates"][platform][0]
            calls.append(row["id"])
            if len(calls) == 1:
                return {
                    "ok": False,
                    "status": "uncertain",
                    "reason": "delivery_receipt_missing",
                    "delivery": {"status": "uncertain"},
                }
            return {
                "ok": True,
                "status": "sent",
                "platform": platform,
                "external_id": row["id"],
                "title": row["title"],
            }

        with (
            patch.object(queue_worker, "CloudState", return_value=cloud),
            patch.object(queue_worker, "run_one", side_effect=fake_run_one),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
        ):
            report = queue_worker.run_daily(send_delay_seconds=0)

        self.assertEqual(len(calls), 1)
        self.assertEqual(report["attempted_count"], 1)
        self.assertEqual(report["sent_count"], 0)
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["results"][0]["status"], "uncertain")
        self.assertEqual(cloud.items[0]["status"], "uncertain")
        self.assertEqual(cloud.items[1]["status"], "queued")
        self.assertFalse(report["ok"])

    def test_owner_digest_rate_limit_stops_after_the_confirmed_media_send(self):
        cloud = FakeCloud()
        calls = []

        def fake_run_one(*_args, **_kwargs):
            calls.append(True)
            return {"ok": True, "status": "sent", "title": "Sent"}

        def rate_limited_notice(_message):
            return {
                "sent": False,
                "reason": "rubika_digest_edit_rejected",
                "diagnostic": {"status": "ERROR", "code": 429},
            }

        with (
            patch.object(queue_worker, "CloudState", return_value=cloud),
            patch.object(queue_worker, "run_one", side_effect=fake_run_one),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
            patch.object(queue_worker, "send_owner_notification", side_effect=rate_limited_notice),
        ):
            report = queue_worker.run_daily(send_delay_seconds=0)

        self.assertEqual(len(calls), 1)
        self.assertEqual(report["sent_count"], 1)
        self.assertEqual(report["status"], "rate_limited")
        self.assertTrue(report["ok"])
        self.assertEqual(cloud.items[0]["status"], "sent")
        self.assertEqual(cloud.items[1]["status"], "queued")

    def test_paused_youtube_item_remains_queued_for_later(self):
        cloud = FakeCloud()
        cloud.items = [make_item("youtube", "abcdefghijk", "YouTube must remain queued")]
        cloud.items[0]["canonical_url"] = "https://www.youtube.com/shorts/abcdefghijk"
        cloud.items[0]["freshness_verified"] = 0

        with (
            patch.object(queue_worker, "CloudState", return_value=cloud),
            patch.object(queue_worker, "run_one") as mocked_run,
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
        ):
            report = queue_worker.run_daily(send_delay_seconds=0)

        mocked_run.assert_not_called()
        self.assertEqual(cloud.items[0]["status"], "queued")
        self.assertEqual(report["queue_counts"]["queued"], 1)
        self.assertEqual(report["results"][0]["status"], "deferred")
        self.assertEqual(report["results"][0]["queue_state"], "left_queued")


if __name__ == "__main__":
    unittest.main()
