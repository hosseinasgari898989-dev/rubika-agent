from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain import queue_worker


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
    def test_counts_four_successes_not_four_attempts_and_leaves_remaining_queue(self):
        def fake_run_one(report_path, platform, output_report, **kwargs):
            payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
            row = payload["candidates"][platform][0]
            if row["title"] == "First failed candidate":
                return {
                    "ok": False, "status": "staging_failed",
                    "reason": "media_validation_failed",
                }
            return {
                "ok": True, "status": "sent",
                "platform": platform, "external_id": row["id"],
                "title": row["title"], "publishing": True,
            }

        with (
            patch.object(queue_worker, "CloudState", FakeCloud),
            patch.object(queue_worker, "run_one", side_effect=fake_run_one),
            patch.object(queue_worker, "_mega_inventory", return_value={
                "available": True, "total": 8, "by_status": {"ready": 2, "sent": 6},
            }),
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
        ):
            report = queue_worker.run_daily(daily_limit=4)

        self.assertEqual(report["sent_count"], 4)
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["attempted_count"], 5)
        self.assertEqual(report["status"], "sent")
        self.assertEqual(report["queue_counts"]["sent"], 4)
        self.assertEqual(report["queue_counts"]["failed"], 1)
        self.assertEqual(report["queue_counts"]["queued"], 1)
        self.assertEqual(report["results"][0]["status"], "staging_failed")
        self.assertEqual(sum(1 for result in report["results"] if result["status"] == "sent"), 4)

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
            report = queue_worker.run_daily(daily_limit=1, max_attempts=1)

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

    def test_stops_after_four_successes_were_already_recorded_today(self):
        cloud = FakeCloud()
        for item in cloud.items[:4]:
            item["status"] = "sent"
        def fake_run_one(*args, **kwargs):
            raise AssertionError("No extra publication attempt should run after daily quota is reached.")

        with (
            patch.object(queue_worker, "CloudState", return_value=cloud),
            patch.object(queue_worker, "run_one", side_effect=fake_run_one) as mocked_run,
            patch.object(queue_worker, "send_owner_notification", return_value={"sent": True}),
            patch.object(queue_worker, "_mega_inventory", return_value={"available": False}),
        ):
            report = queue_worker.run_daily(daily_limit=4)

        mocked_run.assert_not_called()
        self.assertEqual(report["status"], "daily_quota_reached")
        self.assertEqual(report["daily_sent_before"], 4)
        self.assertEqual(report["sent_count"], 0)
        self.assertEqual(report["queue_counts"]["sent_today"], 4)

    def test_worker_main_sends_quota_notification_without_name_error(self):
        messages = []
        report = {
            "ok": True,
            "status": "sent",
            "sent_count": 1,
            "failed_count": 0,
            "skipped_count": 0,
            "attempted_count": 1,
            "daily_sent_today": 1,
            "daily_sent_before": 0,
            "queue_counts": {"queued": 3, "processing": 0, "sent": 1, "failed": 0,
                             "uncertain": 0, "skipped": 0, "sent_today": 1},
            "inventory_text": "📦 وضعیت صف: در انتظار 3",
            "errors": [],
        }
        with tempfile.TemporaryDirectory() as folder:
            report_path = Path(folder) / "report.json"
            with (
                patch("sys.argv", ["queue_worker", "--daily-limit", "1", "--report", str(report_path)]),
                patch.object(queue_worker, "run_daily", return_value=report),
                patch.object(queue_worker, "send_owner_notification",
                             side_effect=lambda message: (messages.append(message) or {"sent": True})),
            ):
                exit_code = queue_worker.main()

            saved = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(messages), 1)
        self.assertIn("1/1", messages[0])
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
            report = queue_worker.run_daily(daily_limit=1, max_attempts=1)

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
            report = queue_worker.run_daily(daily_limit=1, max_attempts=20)

        self.assertEqual(len(calls), 1)
        self.assertEqual(report["attempted_count"], 1)
        self.assertEqual(report["sent_count"], 0)
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["results"][0]["status"], "uncertain")
        self.assertEqual(cloud.items[0]["status"], "uncertain")
        self.assertEqual(cloud.items[1]["status"], "queued")
        self.assertFalse(report["ok"])

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
            report = queue_worker.run_daily(daily_limit=1)

        mocked_run.assert_not_called()
        self.assertEqual(cloud.items[0]["status"], "queued")
        self.assertEqual(report["queue_counts"]["queued"], 1)
        self.assertEqual(report["results"][0]["status"], "deferred")
        self.assertEqual(report["results"][0]["queue_state"], "left_queued")


if __name__ == "__main__":
    unittest.main()
