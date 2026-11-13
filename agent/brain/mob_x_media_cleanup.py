"""Read-only Worker inventory + conservative MEGA cleanup for receipt-confirmed Mob X sends."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from agent.brain.mob_x_link_processor import WorkerClient
from agent.storage.mega import MegaStorage

UNIQUE_OFFICIAL_KEY = re.compile(
    r"^/Rubika-Agent/official/mob_x_[0-9]{15,22}_[a-f0-9]{12}\.(?:jpg|jpeg|png|webp|gif|mp4|m4v|mov|webm|mkv)$"
)
TERMINAL_STATUSES = ("sent",)
PAGE_SIZE = 100
MAX_PAGES_PER_STATUS = 1000


def eligible_terminal_media_key(job: object) -> str | None:
    """Return a unique official key only for a receipt-confirmed terminal send."""
    if not isinstance(job, dict) or job.get("type") != "mob_x_link":
        return None
    status = job.get("status")
    if status not in TERMINAL_STATUSES:
        return None
    result = job.get("result")
    if not isinstance(result, dict):
        return None
    if status == "sent":
        if result.get("delivery_status") != "sent" or not str(result.get("rubika_message_id") or "").strip():
            return None
    key = result.get("storage_key")
    if not isinstance(key, str) or not UNIQUE_OFFICIAL_KEY.fullmatch(key):
        return None
    return key


def _mob_x_jobs(worker: Any, *, page_size: int = PAGE_SIZE) -> list[dict]:
    if type(page_size) is not int or not 1 <= page_size <= PAGE_SIZE:
        raise ValueError("invalid_page_size")
    jobs: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES_PER_STATUS):
        response = worker.request(
            "GET",
            f"/api/console/jobs?type=mob_x_link&limit={page_size}&offset={offset}",
        )
        rows = response.get("items") if isinstance(response, dict) else None
        if not isinstance(rows, list) or len(rows) > page_size:
            raise RuntimeError("worker_mob_x_jobs_response_invalid")
        jobs.extend(row for row in rows if isinstance(row, dict))
        offset += len(rows)
        if len(rows) < page_size:
            break
    else:
        raise RuntimeError("worker_mob_x_jobs_page_limit_exceeded")
    return jobs


def cleanup_terminal_media(worker: Any, storage: Any, *, page_size: int = PAGE_SIZE) -> dict[str, int | str]:
    """Delete only unshared unique media after every referencing job has a confirmed receipt."""
    jobs = _mob_x_jobs(worker, page_size=page_size)
    references: dict[str, list[dict]] = {}
    for job in jobs:
        result = job.get("result") if isinstance(job, dict) else None
        key = result.get("storage_key") if isinstance(result, dict) else None
        if isinstance(key, str) and UNIQUE_OFFICIAL_KEY.fullmatch(key):
            references.setdefault(key, []).append(job)
    candidate_keys = sorted(
        key for key, refs in references.items()
        if all(eligible_terminal_media_key(job) == key for job in refs)
    )
    existing_names = set(storage.list_directory("official"))
    deleted = 0
    already_missing = 0
    failed = 0
    for key in candidate_keys:
        name = Path(key).name
        if name not in existing_names:
            already_missing += 1
            continue
        try:
            storage.delete(key)
            deleted += 1
            existing_names.discard(name)
        except Exception:
            failed += 1
    return {
        "status": "partial" if failed else "ok",
        "jobs_scanned": len(jobs),
        "unique_paths_eligible": len(candidate_keys),
        "deleted_count": deleted,
        "already_missing_count": already_missing,
        "delete_failed_count": failed,
        "d1_writes": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="")
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE)
    args = parser.parse_args(argv)
    try:
        worker = WorkerClient(
            os.getenv("AGENT_API_BASE_URL", "https://rubika-agent.h98609294.workers.dev"),
            os.getenv("AGENT_API_TOKEN", ""),
        )
        storage = MegaStorage(root="/Rubika-Agent", timeout_seconds=180)
        report = cleanup_terminal_media(worker, storage, page_size=args.page_size)
    except Exception as exc:
        report = {
            "status": "failed_closed",
            "error_code": re.sub(r"[^a-z0-9_]", "", type(exc).__name__.lower())[:60] or "cleanup_failed",
            "d1_writes": 0,
        }
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
    print(rendered)
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write("## Mob X terminal-media cleanup\n\n")
            for key in ("status", "jobs_scanned", "unique_paths_eligible", "deleted_count",
                        "already_missing_count", "delete_failed_count", "d1_writes"):
                if key in report:
                    stream.write(f"- {key}: {report[key]}\n")
            stream.write("- uncertain/sending/ready_for_review/failed/rejected: retained\n")
            stream.write("- legacy shared media names: retained\n")
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
