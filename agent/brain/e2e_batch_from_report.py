"""Process a bounded batch of newly discovered fan edits through MEGA/D1 to Rubika.

The current batch is a controlled test over the latest discovery report. It does not
yet implement the future persistent backlog or a daily scheduler.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.brain.e2e_from_report import _matches_poppy_edit, _safe_error, run_one
from agent.brain.models import Candidate
from agent.brain.orchestrator import candidates_from_report


PLATFORM_ORDER = ("tiktok", "youtube")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_rows(report_path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("discovery_report_unreadable") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), dict):
        raise ValueError("invalid_discovery_report")

    rows_by_identity: dict[str, dict[str, Any]] = {}
    for platform in PLATFORM_ORDER:
        rows = payload["candidates"].get(platform, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            item_id = row.get("id") or row.get("external_id")
            url = row.get("url") or row.get("canonical_url")
            if isinstance(item_id, str) and item_id and isinstance(url, str) and url:
                rows_by_identity.setdefault(f"{platform}:{item_id}", row)
    return payload, rows_by_identity


def _round_robin_candidates(
    candidates: list[Candidate],
    *,
    platforms: tuple[str, ...],
) -> list[Candidate]:
    """Keep discovery order within each platform while alternating platforms."""
    groups: dict[str, list[Candidate]] = {platform: [] for platform in platforms}
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.platform not in groups or not _matches_poppy_edit(candidate):
            continue
        identity = f"{candidate.platform}:{candidate.external_id}"
        if identity in seen:
            continue
        seen.add(identity)
        groups[candidate.platform].append(candidate)

    ordered: list[Candidate] = []
    index = 0
    while True:
        added = False
        for platform in platforms:
            if index < len(groups[platform]):
                ordered.append(groups[platform][index])
                added = True
        if not added:
            break
        index += 1
    return ordered


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_batch(
    report_path: Path,
    output_report: Path,
    *,
    platform: str = "both",
    daily_limit: int = 4,
) -> dict[str, Any]:
    if platform not in {"tiktok", "youtube", "both"}:
        raise ValueError("unsupported_platform")
    if type(daily_limit) is not int or not 1 <= daily_limit <= 4:
        raise ValueError("daily_limit_must_be_between_1_and_4")

    platforms = PLATFORM_ORDER if platform == "both" else (platform,)
    payload, rows_by_identity = _load_rows(report_path)
    candidates = candidates_from_report(report_path)
    ordered = _round_robin_candidates(candidates, platforms=platforms)

    unavailable: dict[str, str] = {}
    unverified_youtube_count = 0
    if "youtube" in platforms and (
        not os.getenv("RELAY_BASE_URL", "").strip()
        or not os.getenv("RELAY_API_TOKEN", "").strip()
    ):
        unavailable["youtube"] = "youtube_relay_not_configured"
        ordered = [candidate for candidate in ordered if candidate.platform != "youtube"]
    elif "youtube" in platforms:
        verified_ordered: list[Candidate] = []
        for candidate in ordered:
            if candidate.platform != "youtube":
                verified_ordered.append(candidate)
                continue
            identity = f"{candidate.platform}:{candidate.external_id}"
            row = rows_by_identity.get(identity, {})
            if row.get("freshness_verified") is True:
                verified_ordered.append(candidate)
            else:
                unverified_youtube_count += 1
        ordered = verified_ordered
        if unverified_youtube_count:
            unavailable["youtube"] = "publication_date_unverified"

    summary: dict[str, Any] = {
        "ok": True,
        "status": "running",
        "created_at": _utc_now(),
        "source_report": report_path.name,
        "platform": platform,
        "daily_limit": daily_limit,
        "candidate_count_in_report": len(candidates),
        "eligible_relevant_candidates": len(ordered),
        "attempted_count": 0,
        "sent_count": 0,
        "failed_count": 0,
        "publishing": False,
        "unavailable_platforms": unavailable,
        "unverified_youtube_candidates_skipped": unverified_youtube_count,
        "results": [],
    }
    attempted_ids: set[str] = set()
    output_report.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output_report, summary)

    with tempfile.TemporaryDirectory(prefix="rubika-agent-batch-") as temp_dir:
        temp_path = Path(temp_dir)
        for candidate in ordered:
            if summary["attempted_count"] >= daily_limit:
                break
            identity = f"{candidate.platform}:{candidate.external_id}"
            if identity in attempted_ids:
                continue
            attempted_ids.add(identity)

            source_row = rows_by_identity.get(identity)
            if source_row is None:
                continue
            single_payload = dict(payload)
            single_payload["candidates"] = {
                "tiktok": [], "youtube": [], "instagram": []
            }
            single_payload["candidates"][candidate.platform] = [source_row]
            single_report = temp_path / f"{candidate.platform}-{candidate.external_id}.json"
            single_report.write_text(
                json.dumps(single_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            try:
                result = run_one(
                    single_report,
                    candidate.platform,
                    temp_path / f"result-{candidate.platform}-{candidate.external_id}.json",
                    candidate_limit=1,
                    candidate_offset=0,
                )
            except Exception as exc:
                result = {
                    "ok": False,
                    "status": "error",
                    "platform": candidate.platform,
                    "external_id": candidate.external_id,
                    "title": candidate.title,
                    "reason": _safe_error(exc),
                    "publishing": False,
                }

            result = dict(result)
            result.setdefault("platform", candidate.platform)
            result.setdefault("external_id", candidate.external_id)
            result.setdefault("title", candidate.title)
            result["canonical_url"] = candidate.canonical_url

            # Already-known or duplicate rows do not consume one of the four
            # processing slots. The cloud seen index and MEGA ledger remain authoritative.
            if result.get("status") != "no_candidate":
                summary["attempted_count"] += 1
                if result.get("status") == "sent":
                    summary["sent_count"] += 1
                else:
                    summary["failed_count"] += 1
            summary["results"].append(result)
            summary["publishing"] = summary["sent_count"] > 0
            _write_json(output_report, summary)

    if summary["failed_count"]:
        summary["status"] = "partial" if summary["sent_count"] else "failed"
        summary["ok"] = False
    elif summary["sent_count"]:
        summary["status"] = "sent"
    else:
        summary["status"] = "no_candidate"
    summary["finished_at"] = _utc_now()
    _write_json(output_report, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("artifacts/termux_discovery.json"))
    parser.add_argument("--platform", choices=("tiktok", "youtube", "both"), default="both")
    parser.add_argument("--daily-limit", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--output-report", type=Path, default=Path("artifacts/brain-e2e-report.json"))
    args = parser.parse_args()

    try:
        result = run_batch(
            args.report,
            args.output_report,
            platform=args.platform,
            daily_limit=args.daily_limit,
        )
    except Exception as exc:
        result = {
            "ok": False,
            "status": "error",
            "error": _safe_error(exc),
            "publishing": False,
            "updated_at": _utc_now(),
        }
        args.output_report.parent.mkdir(parents=True, exist_ok=True)
        _write_json(args.output_report, result)

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
