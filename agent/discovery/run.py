"""Unified metadata discovery; never downloads or publishes media."""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.brain.tiktok_urls import normalize_tiktok_url
from agent.source_policy import known_tiktok_profiles, resolve_source_policy


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "artifacts" / "discovery_report.json"
TERMUX_REPORT_PATH = ROOT / "artifacts" / "termux_discovery.json"


def _unique_sorted(items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items.values(), key=lambda item: (
        str(item.get("title") or "").casefold(),
        str(item.get("id") or ""),
    ))


def _load_phone_report(path: str | Path) -> tuple[dict[str, list[dict[str, Any]]], str | None, str]:
    """Load and strictly validate the phone's metadata report, if present."""
    report_path = Path(path)
    if not report_path.exists():
        return {"tiktok": []}, None, "not_found"
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"tiktok": []}, None, "unreadable"
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("source") not in {"termux_phone", "github_actions_termux_docker"}
        or payload.get("mode") != "metadata_only_no_download_no_publish"
        or not isinstance(payload.get("candidates"), dict)
    ):
        return {"tiktok": []}, None, "invalid_report"

    accepted: dict[str, list[dict[str, Any]]] = {"tiktok": []}
    for platform in accepted:
        raw_rows = payload["candidates"].get(platform, [])
        if not isinstance(raw_rows, list):
            continue
        seen_ids: set[str] = set()
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            item_id = row.get("id")
            raw_url = row.get("url")
            if not isinstance(item_id, str) or not isinstance(raw_url, str):
                continue
            url = raw_url.strip()
            canonical_id = ""
            if platform == "tiktok":
                identity = normalize_tiktok_url(url)
                selected = {handle.casefold() for handle in known_tiktok_profiles()}
                if identity is not None and identity[0].casefold() in selected:
                    canonical_id = identity[1]
                    match = None
            if not canonical_id or canonical_id != item_id or item_id in seen_ids:
                continue
            safe = dict(row)
            safe["platform"] = platform
            safe["id"] = canonical_id
            if platform == "tiktok":
                identity = normalize_tiktok_url(url)
                if identity is None or identity[1] != canonical_id:
                    continue
                source_policy = resolve_source_policy("tiktok", identity[0])
                if source_policy.get("known_source") is not True:
                    continue
                safe["url"] = identity[2]
                safe["author"] = identity[0]
                safe["source_policy"] = source_policy
            safe["title"] = str(safe.get("title") or "")[:1200]
            safe["caption"] = str(safe.get("caption") or "")[:4000]
            safe["author"] = str(safe.get("author") or "")[:100]
            safe["method"] = str(safe.get("method") or "github_actions_termux_docker")[:80]
            seen_ids.add(item_id)
            accepted[platform].append(safe)
    return accepted, str(payload.get("generated_at") or "") or None, "loaded"


def run_discovery(
    *,
    report_path: str | Path = REPORT_PATH,
    phone_report_path: str | Path = TERMUX_REPORT_PATH,
) -> dict[str, Any]:
    """Merge verified phone metadata with controlled fallbacks and write diagnostic evidence."""
    ignored = {"tiktok": set()}
    store: dict[str, dict[str, dict[str, Any]]] = {
        "tiktok": {},
    }
    seen = {
        "tiktok": set(ignored.get("tiktok", set())),
    }

    phone_candidates, phone_generated_at, phone_status = _load_phone_report(phone_report_path)
    phone_report_loaded = phone_status == "loaded"
    termux_only = os.getenv("TERMUX_ONLY", "").strip().lower() in {"1", "true", "yes"}
    phone_attempts: list[dict[str, Any]] = [{
        "platform": "multi",
        "method": "termux_phone_report",
        "result": phone_status,
        "generated_at": phone_generated_at,
    }]
    for platform, rows in phone_candidates.items():
        for row in rows:
            item_id = row["id"]
            if item_id in seen[platform]:
                continue
            store[platform][item_id] = row
            seen[platform].add(item_id)

    if phone_report_loaded:
        print("Discovery: validated phone report loaded; using phone metadata only.", flush=True)
    elif termux_only:
        print(f"Discovery: TERMUX_ONLY is enabled but the phone report is {phone_status}; no hosted search will run.", flush=True)
    else:
        print("Discovery: no valid phone report; using the legacy metadata-only fallbacks.", flush=True)

    candidates = {key: _unique_sorted(values) for key, values in store.items()}
    summary = {
        "tiktok_candidates": len(candidates["tiktok"]),
        "core_search_target_met": bool(candidates["tiktok"]),
        "termux_report_loaded": phone_report_loaded,
        "termux_report_status": phone_status,
        "termux_report_generated_at": phone_generated_at,
        "termux_only": termux_only,
        "reason_phone_report_missing": "phone_report_missing_or_invalid" if termux_only and not phone_report_loaded else None,
        "ready_to_publish": False,
        "reason_not_publish_ready": (
            "metadata_only; downloading, duplicate-content inspection, reuse permission, "
            "media moderation, complete-batch checks and explicit publish authorization are separate gates"
        ),
    }
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "metadata_only_no_download_no_publish",
        "summary": summary,
        "excluded_existing_ids": {key: len(value) for key, value in ignored.items()},
        "candidates": candidates,
        "attempts": (
            phone_attempts
        ),
    }
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    print(f"Discovery report written: {path}", flush=True)
    return report


def self_test() -> int:
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    report = run_discovery()
    # A green workflow must mean the required metadata targets really were found.
    return 0 if report.get("summary", {}).get("core_search_target_met") else 2


if __name__ == "__main__":
    raise SystemExit(main())
