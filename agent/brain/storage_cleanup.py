"""Conservative MEGA media cleanup.

Only removes media in /Rubika-Agent/staging that the durable ledger marks as
successfully sent, older than a safety window, and not referenced by any
non-sent ledger item. Dry-run is the default. Never deletes ledger snapshots.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from agent.brain.ledger import MegaLedger
from agent.storage.mega import MegaStorage


VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".webm"}
DEFAULT_MIN_AGE_HOURS = 24
DEFAULT_MAX_DELETE = 25


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now_must_be_timezone_aware")
    return current.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _staging_media_key(storage: Any, value: Any) -> tuple[str, str] | None:
    """Return (remote path, basename) only for a direct video child of staging."""
    if not isinstance(value, str) or not value:
        return None
    root = str(getattr(storage, "root", "/Rubika-Agent")).rstrip("/")
    prefix = root + "/staging/"
    if not value.startswith(prefix):
        return None
    relative = value[len(prefix):]
    path = PurePosixPath(relative)
    if not relative or path.name != relative or path.name in {".", ".."}:
        return None
    if path.suffix.casefold() not in VIDEO_SUFFIXES:
        return None
    return value, path.name


def cleanup_stale_sent_media(
    storage: Any,
    state: Mapping[str, Any],
    *,
    apply: bool = False,
    min_age_hours: int = DEFAULT_MIN_AGE_HOURS,
    max_delete: int = DEFAULT_MAX_DELETE,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Plan or execute a bounded safe cleanup, failing closed on uncertain state."""
    if not isinstance(state, Mapping) or not isinstance(state.get("items"), Mapping):
        raise ValueError("ledger_items_must_be_object")
    if type(min_age_hours) is not int or not 24 <= min_age_hours <= 8760:
        raise ValueError("min_age_hours_must_be_between_24_and_8760")
    if type(max_delete) is not int or not 1 <= max_delete <= 100:
        raise ValueError("max_delete_must_be_between_1_and_100")

    current = _utc(now)
    cutoff = current - timedelta(hours=min_age_hours)
    items = state["items"]
    references: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    all_referenced_names: set[str] = set()

    for item in items.values():
        if not isinstance(item, Mapping):
            continue
        parsed_key = _staging_media_key(storage, item.get("storage_key"))
        if parsed_key is None:
            continue
        remote_path, basename = parsed_key
        references[remote_path].append(item)
        all_referenced_names.add(basename)

    # If MEGA's listing fails, the caller raises and no deletion is attempted.
    listed_names = set(storage.list_directory("staging"))
    existing_video_names = {
        name for name in listed_names
        if isinstance(name, str) and PurePosixPath(name).suffix.casefold() in VIDEO_SUFFIXES
        and PurePosixPath(name).name == name and name not in {".", ".."}
    }

    reasons: Counter[str] = Counter()
    eligible_paths: list[str] = []
    for remote_path, records in references.items():
        basename = PurePosixPath(remote_path).name
        if basename not in existing_video_names:
            reasons["already_missing"] += 1
            continue
        if not records or any(record.get("status") != "sent" for record in records):
            reasons["referenced_by_non_sent_item"] += 1
            continue
        sent_times = [_parse_timestamp(record.get("sent_at")) for record in records]
        if not sent_times or any(value is None for value in sent_times):
            reasons["missing_or_invalid_sent_timestamp"] += 1
            continue
        if any(value > cutoff for value in sent_times if value is not None):
            reasons["inside_safety_window"] += 1
            continue
        eligible_paths.append(remote_path)

    eligible_paths.sort()
    selected_paths = eligible_paths[:max_delete]
    deferred_count = max(0, len(eligible_paths) - len(selected_paths))
    deleted: list[str] = []
    errors: list[dict[str, str]] = []

    if apply:
        for remote_path in selected_paths:
            try:
                storage.delete(remote_path)
                deleted.append(remote_path)
            except Exception as exc:
                errors.append({
                    "path": remote_path,
                    "error_code": "".join(
                        ch for ch in type(exc).__name__ if ch.isalnum() or ch in "_-"
                    )[:80] or "cleanup_error",
                })

    planned_count = len(selected_paths)
    report = {
        "ok": not errors,
        "status": "partial" if errors or deferred_count else "ok",
        "mode": "apply" if apply else "dry_run",
        "generated_at": current.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "min_age_hours": min_age_hours,
        "max_delete": max_delete,
        "staging_video_files_seen": len(existing_video_names),
        "ledger_referenced_video_files": len(all_referenced_names),
        "untracked_video_files_retained": len(existing_video_names - all_referenced_names),
        "planned_delete_count": planned_count,
        "deleted_count": len(deleted),
        "deferred_over_limit_count": deferred_count,
        "retained_counts_by_reason": dict(sorted(reasons.items())),
        "deleted_paths": deleted,
        "planned_paths": selected_paths if not apply else [],
        "errors": errors,
        "safety": {
            "only_direct_children_of_staging": True,
            "requires_ledger_status_sent": True,
            "requires_all_references_sent": True,
            "requires_valid_sent_timestamp_older_than_window": True,
            "protects_failed_and_uncertain_items": True,
            "protects_state_and_all_non_media_files": True,
            "untracked_files_retained": True,
            "ledger_snapshots_modified": False,
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="perform safe deletions; without this option, only report the plan",
    )
    parser.add_argument("--min-age-hours", type=int, default=DEFAULT_MIN_AGE_HOURS)
    parser.add_argument("--max-delete", type=int, default=DEFAULT_MAX_DELETE)
    parser.add_argument("--output-report", type=Path, default=None)
    args = parser.parse_args()

    try:
        storage = MegaStorage(root="/Rubika-Agent", timeout_seconds=180)
        state = MegaLedger(storage).load()
        report = cleanup_stale_sent_media(
            storage, state, apply=args.apply,
            min_age_hours=args.min_age_hours, max_delete=args.max_delete,
        )
    except Exception as exc:
        report = {
            "ok": False,
            "status": "failed_closed",
            "mode": "apply" if args.apply else "dry_run",
            "deleted_count": 0,
            "error_code": "".join(
                ch for ch in type(exc).__name__ if ch.isalnum() or ch in "_-"
            )[:80] or "cleanup_failed",
            "safety": "No deletion is attempted when the ledger or MEGA listing cannot be read.",
        }

    if args.output_report is not None:
        args.output_report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output_report.with_suffix(args.output_report.suffix + ".tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(args.output_report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
