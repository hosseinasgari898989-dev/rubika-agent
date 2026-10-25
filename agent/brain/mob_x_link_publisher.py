"""One-shot publisher for explicitly owner-approved Mob X previews.

This module is gated by an explicit Actions variable and exact test-channel ID.
A channel send is never retried. Any ambiguous outcome is recorded as uncertain
and a private owner notice offers manual confirmation only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from agent.brain.mob_x_link_processor import MobXLinkError, WorkerClient
from agent.brain.mob_x_rubika import send_test_channel_once, send_uncertain_notice

TEST_CHANNEL_GUID = "c0EFohK0f3174377fb6fa422d3ccb230"
JOB_ID_RE = re.compile(r"job_[A-Fa-f0-9]{8,80}")


def _safe_code(value: object, fallback: str = "mob_x_publish_failed") -> str:
    candidate = str(value or "").lower()
    return candidate if re.fullmatch(r"[a-z0-9_-]{1,80}", candidate) else fallback


def _notify_uncertain(token: str, owner_chat: str, job_id: str, error_code: str) -> bool:
    try:
        send_uncertain_notice(
            token,
            owner_chat,
            job_id,
            "⚠️ نتیجهٔ ارسال خبر Mob در کانال تست نامشخص است (" + error_code + "). ارسال خودکار تکرار نمی‌شود. بعد از بررسی کانال، فقط اگر پست واقعاً منتشر شده است، دکمهٔ تأیید دستی را بزن.",
        )
        return True
    except Exception:
        return False


def publish_one(
    *,
    client: WorkerClient | None = None,
    sender: Any = None,
    notifier: Any = None,
    report_path: str | Path | None = None,
) -> dict:
    report: dict[str, Any] = {"mode": "publish_one", "publishing_attempted": False, "channel": "test"}
    if str(os.getenv("RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014", "")).strip().lower() != "approved":
        report.update(status="idle", reason="mob_x_owner_publish_gate_not_approved")
        return _write_report(report, report_path, 0)

    try:
        source_config = json.loads(Path("config/mob_official_sources.json").read_text(encoding="utf-8"))
        publishing = source_config.get("publishing", {})
    except Exception:
        report.update(status="blocked", error_code="mob_x_publish_config_unavailable")
        return _write_report(report, report_path, 2)
    if not (
        publishing.get("enabled") is True
        and publishing.get("mode") == "production"
        and publishing.get("production_enabled") is True
        and publishing.get("target_channel_guid") == TEST_CHANNEL_GUID
        and publishing.get("max_posts_per_run") == 1
    ):
        report.update(status="blocked", error_code="mob_x_publish_config_guard_not_enabled")
        return _write_report(report, report_path, 2)

    token = os.getenv("RUBIKA_TOKEN", "").strip()
    owner_chat = os.getenv("RUBIKA_OWNER_CHAT_ID", "").strip()
    channel = os.getenv("RUBIKA_CHANNEL_GUID", "").strip()
    if not token or not owner_chat:
        report.update(status="blocked", error_code="mob_x_publish_credentials_missing")
        return _write_report(report, report_path, 2)
    if channel != TEST_CHANNEL_GUID:
        report.update(status="blocked", error_code="rubika_channel_not_exact_test_target")
        return _write_report(report, report_path, 2)

    worker = client
    try:
        if worker is None:
            worker = WorkerClient(
                os.getenv("AGENT_API_BASE_URL", "https://rubika-agent.h98609294.workers.dev"),
                os.getenv("AGENT_API_TOKEN", ""),
            )
        # The Worker owns the global publishing capability. Never claim work unless
        # /api/status explicitly reports capabilities.publishing=true.
        try:
            status_payload = worker.request("GET", "/api/status")
        except Exception:
            report.update(status="blocked", error_code="publishing_disabled", capability_check="unavailable")
            return _write_report(report, report_path, 1)
        capabilities = status_payload.get("capabilities") if isinstance(status_payload, dict) else None
        if not isinstance(capabilities, dict) or capabilities.get("publishing") is not True:
            report.update(status="blocked", error_code="publishing_disabled", capability_check="disabled_or_invalid")
            return _write_report(report, report_path, 1)

        rules = worker.rules()
        if rules.get("test_channel_only") is not True:
            report.update(status="blocked", error_code="console_test_channel_guard_not_enabled")
            return _write_report(report, report_path, 2)

        claim = worker.request("POST", "/api/console/mob-x/publish-claim", {})
        if claim.get("claimed") is not True:
            report.update(status="idle", reason=_safe_code(claim.get("reason"), "no_owner_approved_mob_x_job"))
            return _write_report(report, report_path, 0)
        job = claim.get("job") if isinstance(claim.get("job"), dict) else {}
        job_id = str(job.get("id") or "")
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        if not JOB_ID_RE.fullmatch(job_id):
            report.update(status="failed", error_code="mob_x_publish_job_id_invalid")
            return _write_report(report, report_path, 1)
        report.update(job_id=job_id, status="sending", channel="test")
        file_id = str(result.get("rubika_file_id") or "")
        caption = str(result.get("caption") or "")
        item_id = str(result.get("item_id") or "")
        external_id = str(result.get("external_id") or "")
        if not file_id or not caption.strip() or not item_id or not re.fullmatch(r"[0-9]{15,22}", external_id):
            code = "mob_x_approved_payload_incomplete"
            try:
                worker.request("POST", "/api/console/mob-x/jobs/" + job_id + "/delivery",
                               {"status": "failed", "error_code": code})
            except Exception:
                pass
            report.update(status="failed", error_code=code)
            return _write_report(report, report_path, 1)

        send = sender or send_test_channel_once
        report["publishing_attempted"] = True
        try:
            message_id = send(token, channel, file_id, caption)
        except Exception:
            # The HTTP send may have reached Rubika even when the client raised.
            # This is deliberately one attempt and is never retried.
            message_id = None
            uncertain_code = "rubika_send_outcome_unknown"
        else:
            uncertain_code = "rubika_send_receipt_missing" if not message_id else None

        if not message_id:
            notify_fn = notifier or _notify_uncertain
            try:
                worker.request(
                    "POST", "/api/console/mob-x/jobs/" + job_id + "/delivery",
                    {"status": "uncertain", "error_code": uncertain_code or "rubika_send_outcome_unknown"},
                )
            except Exception:
                report["delivery_state_update_failed"] = True
            notice_sent = False
            try:
                notice_sent = bool(notify_fn(token, owner_chat, job_id, uncertain_code or "rubika_send_outcome_unknown"))
            except Exception:
                notice_sent = False
            report.update(
                status="uncertain",
                error_code=uncertain_code or "rubika_send_outcome_unknown",
                owner_notified=notice_sent,
                automatic_retry=False,
            )
            return _write_report(report, report_path, 0)

        try:
            worker.request(
                "POST", "/api/console/mob-x/jobs/" + job_id + "/delivery",
                {"status": "sent", "message_id": str(message_id)},
            )
            report.update(status="sent", message_id=str(message_id))
        except Exception:
            # Never send again if the status write is lost after a successful send.
            # A second status-only attempt can make a still-sending job uncertain;
            # it never invokes the Rubika channel sender.
            try:
                worker.request(
                    "POST", "/api/console/mob-x/jobs/" + job_id + "/delivery",
                    {"status": "uncertain", "error_code": "mob_x_delivery_status_write_failed"},
                )
            except Exception:
                report["delivery_state_update_failed"] = True
            notice_sent = _notify_uncertain(token, owner_chat, job_id, "mob_x_delivery_status_write_failed")
            report.update(status="uncertain", message_id=str(message_id), owner_notified=notice_sent, automatic_retry=False)
        return _write_report(report, report_path, 0)
    except MobXLinkError as exc:
        report.update(status="failed", error_code=_safe_code(exc.code))
        return _write_report(report, report_path, 1)
    except Exception:
        report.update(status="failed", error_code="mob_x_publisher_internal_error")
        return _write_report(report, report_path, 1)


def _write_report(report: dict, path: str | Path | None, exit_code: int) -> dict:
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
    print(rendered)
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write("## Mob X test-channel publisher\n\n")
            stream.write(f"- Status: {report.get('status', 'unknown')}\n")
            if report.get("error_code"):
                stream.write(f"- Error code: {report['error_code']}\n")
            stream.write("- Destination: test channel only\n")
            stream.write(f"- Automatic retry: {str(report.get('automatic_retry', False)).lower()}\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish-one", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)
    if not args.publish_one:
        parser.error("choose --publish-one")
    report = publish_one(report_path=args.report or None)
    return 0 if report.get("status") in {"idle", "sent", "uncertain", "sent_status_write_unknown"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
