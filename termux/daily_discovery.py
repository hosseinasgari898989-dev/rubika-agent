"""Metadata-only collector run inside Termux Docker by GitHub Actions.

The hosted workflow owns report commits and queue intake. This module does not push
to GitHub, download media, use paid AI providers, or publish to Rubika.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.discovery.mob_official_complete import discover_official_x_posts
from agent.discovery.termux_sources import TIKTOK_PROFILES, discover_tiktok_profiles
from agent.lifecycle import filter_candidates_by_birth, load_lifecycle_policy
from agent.brain.cloud_state import CloudState, CloudStateError, resolve_agent_api_token
from agent.source_policy import set_dynamic_tiktok_sources


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "artifacts" / "termux_discovery.json"


def _console_source_failure_reason(error: Exception) -> str:
    """Map remote-source failures to bounded reason codes; never retain exception text."""
    if isinstance(error, CloudStateError):
        status = error.status_code
        if status == 401:
            return "http_401"
        if status == 403:
            return "http_403"
        if status == 404:
            return "http_404"
        if status is not None and 500 <= status <= 599:
            return "http_5xx"
        if error.code == "control_api_timeout":
            return "timeout"
        if error.code in {
            "control_api_invalid_json",
            "control_api_invalid_response",
            "control_api_invalid_tiktok_sources",
        }:
            return "invalid_json"
        if status is not None:
            return "http_other"
        return "unavailable"
    if isinstance(error, (RuntimeError, ValueError)) and str(error) in {
        "token_missing",
        "token_missing_zero",
        "token_missing_short",
        "console_sources_auth_unavailable",
        "missing_or_short_AGENT_API_TOKEN",
    }:
        return str(error) if str(error).startswith("token_missing_") else "token_missing"
    return "unavailable"


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            temp_path = Path(stream.name)
        temp_path.replace(path)
    except OSError:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def collect_daily_report(*, output_path: str | Path = REPORT_PATH, load_remote_sources: bool = False) -> dict[str, Any]:
    """Collect TikTok edit metadata and official Mob X posts without downloads or publishing."""
    print("1/2 TikTok: loading owner-selected creator profiles (metadata only)...", flush=True)
    dynamic_cutoffs: dict[str, str] = {}
    missing_cutoff_attempts: list[dict[str, str]] = []
    source_diagnostics: list[dict[str, str]] = []
    source_rows_available = False
    source_status = "legacy_six_local"
    source_overlay_reason: str | None = None
    token, token_length_bucket = resolve_agent_api_token()

    if load_remote_sources:
        try:
            if not token:
                raise RuntimeError("token_missing_" + token_length_bucket)
            cloud = CloudState(
                os.getenv("AGENT_API_BASE_URL", "https://rubika-agent.h98609294.workers.dev"),
                token=token, timeout_seconds=25,
            )
            # Keep CloudStateError intact so API/timeout/JSON diagnostics survive.
            source_rows = cloud.get_console_tiktok_sources()
            if not isinstance(source_rows, list):
                raise CloudStateError(
                    "control_api_invalid_tiktok_sources",
                    endpoint_path="/api/console/sources",
                )
            set_dynamic_tiktok_sources(source_rows)
            source_rows_available = True
            selected_profiles: list[str] = []
            for row in source_rows:
                if not isinstance(row, dict) or not (row.get("enabled") is True or row.get("enabled") == 1):
                    continue
                handle = str(row.get("source_key") or "").strip().lstrip("@")
                if not handle:
                    continue
                if row.get("managed") is True:
                    created_at = row.get("created_at")
                    cutoff_valid = False
                    if isinstance(created_at, str) and created_at.strip():
                        try:
                            datetime.fromisoformat(created_at.strip().replace("Z", "+00:00"))
                            cutoff_valid = True
                        except ValueError:
                            cutoff_valid = False
                    if not cutoff_valid:
                        missing_cutoff_attempts.append({
                            "platform": "tiktok",
                            "source": "console_sources",
                            "query": "@" + handle[:24],
                            "reason": "missing_created_at_cutoff",
                        })
                        continue
                    dynamic_cutoffs[handle.casefold()] = created_at.strip()
                selected_profiles.append(handle)
            profiles = tuple(selected_profiles)
            source_status = "console_sources_loaded"
        except Exception as exc:
            # Keep fallback behavior, but persist only a bounded code, never exception text.
            source_overlay_reason = _console_source_failure_reason(exc)
            set_dynamic_tiktok_sources([])
            dynamic_cutoffs = {}
            profiles = TIKTOK_PROFILES
            source_rows_available = False
            source_status = "console_sources_unavailable_using_legacy_six"
            source_diagnostics.append({
                "source": "tiktok_console_sources",
                "reason": source_overlay_reason,
            })
    else:
        set_dynamic_tiktok_sources([])
        profiles = TIKTOK_PROFILES

    max_tiktok_results = max(48, len(profiles) * 8) if profiles else 0
    if not profiles:
        tiktok_candidates, tiktok_attempts = [], [
            {"platform": "tiktok", "result": "no_enabled_sources"}
        ]
    else:
        tiktok_candidates, tiktok_attempts = discover_tiktok_profiles(
            profiles=profiles,
            selected_profiles=profiles,
            max_results=max_tiktok_results,
            max_per_profile=8,
            created_after_by_profile=dynamic_cutoffs,
        )
    tiktok_attempts.extend(missing_cutoff_attempts)
    tiktok_attempts.extend(source_diagnostics)
    print("2/2 Mob Entertainment: checking only official X posts via public discovery and X syndication (metadata only)...", flush=True)
    mob_candidates, mob_attempts = discover_official_x_posts(max_results=12)

    lifecycle_policy = load_lifecycle_policy()
    # The lifecycle birth boundary applies to the separate official Mob-news lane only.
    mob_candidates, stale_mob = filter_candidates_by_birth(
        mob_candidates, policy=lifecycle_policy
    )
    rejected_candidates: list[dict[str, Any]] = []

    def record_rejection(row: Any, reason: str, platform: str) -> None:
        if len(rejected_candidates) >= 100 or not isinstance(row, dict):
            return
        external_id = row.get("external_id") or row.get("id")
        if not isinstance(external_id, (str, int)) or not str(external_id).strip():
            return
        created_at = row.get("created_at") or row.get("published_at")
        rejected_candidates.append({
            "platform": platform,
            "external_id": str(external_id)[:120],
            "reason": reason[:80],
            "created_at": str(created_at)[:80] if created_at is not None else None,
        })

    # Preserve the source-level rejected identities before normalizing the survivors.
    for attempt in [*tiktok_attempts, *mob_attempts]:
        if not isinstance(attempt, dict):
            continue
        entries = attempt.get("rejected_candidates")
        if not isinstance(entries, list):
            continue
        for rejected in entries:
            if len(rejected_candidates) >= 100:
                break
            if not isinstance(rejected, dict):
                continue
            platform = str(rejected.get("platform") or ("tiktok" if attempt in tiktok_attempts else "mob_official"))
            record_rejection(rejected, str(rejected.get("reason") or "source_rejected"), platform)

    for row in stale_mob:
        record_rejection(row, "before_lifecycle_birth_cutoff", "mob_official")

    lifecycle_attempt = {
        "source": "agent_lifecycle",
        "mode": lifecycle_policy["mode"],
        "birth_cutoff_enabled": lifecycle_policy["mode"] == "production",
        "mob_official_rejected": len(stale_mob),
    }

    lists = {
        "tiktok": tiktok_candidates,
        "mob_official": mob_candidates,
    }
    normalized: dict[str, list[dict[str, Any]]] = {}
    for platform, rows in lists.items():
        seen: set[str] = set()
        normalized[platform] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("platform") != platform:
                record_rejection(row, "candidate_platform_or_shape_invalid", platform)
                continue
            item_id = row.get("id") or row.get("external_id")
            url = row.get("url") or row.get("canonical_url")
            if not isinstance(item_id, str) or not item_id or not isinstance(url, str):
                record_rejection(row, "candidate_identity_or_url_missing", platform)
                continue
            if platform == "tiktok" and not (isinstance(row.get("source_policy"), dict) and row["source_policy"].get("known_source") is True):
                record_rejection(row, "source_not_tracked", platform)
                continue
            if item_id in seen:
                record_rejection(row, "duplicate_candidate_in_run", platform)
                continue
            seen.add(item_id)
            normalized[platform].append(row)

    now = datetime.now(timezone.utc)
    provider_timestamps: dict[str, str | None] = {
        "x-cli": None,
        "FxTwitter": None,
        "VxTwitter": None,
    }
    for attempt in mob_attempts:
        if not isinstance(attempt, dict) or attempt.get("source") != "mob_x_provider_freshness":
            continue
        raw = attempt.get("newest_created_at_returned")
        if not isinstance(raw, dict):
            continue
        provider_timestamps["x-cli"] = raw.get("x-cli")
        provider_timestamps["FxTwitter"] = raw.get("fxtwitter")
        provider_timestamps["VxTwitter"] = raw.get("vxtwitter")

    source_freshness: dict[str, dict[str, Any]] = {}
    for provider, created_at in provider_timestamps.items():
        lag_seconds: int | None = None
        if isinstance(created_at, str) and created_at:
            try:
                timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                lag_seconds = max(0, int((now - timestamp.astimezone(timezone.utc)).total_seconds()))
            except ValueError:
                created_at = None
        source_freshness[provider] = {
            "newest_created_at_returned": created_at,
            "lag_seconds": lag_seconds,
        }

    report = {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "source": "github_actions_termux_docker",
        "mode": "metadata_only_no_download_no_publish",
        "lifecycle": {
            "mode": lifecycle_policy["mode"],
            "born_at": lifecycle_policy["born_at"],
            "birth_cutoff_enabled": lifecycle_policy["mode"] == "production",
        },
        "summary": {
            "tiktok_candidates": len(normalized["tiktok"]),
            "mob_official_candidates": len(normalized["mob_official"]),
            "mob_official_source": "Mob Entertainment official sources",
            "mob_official_method": "official_x_syndication_and_verified_seed_urls",
            "mob_official_birth_cutoff_rejected": len(stale_mob),
            "tiktok_selected_source_candidates": len(normalized["tiktok"]),
            "tiktok_method": "yt_dlp_selected_creator_profiles_only",
            "tiktok_source_overlay_status": source_status,
            "tiktok_source_overlay_reason": source_overlay_reason,
            "tiktok_token_length_bucket": token_length_bucket,
            "tiktok_account_removals_applied": source_rows_available if load_remote_sources else None,
            "tiktok_missing_created_at_cutoff_count": len(missing_cutoff_attempts),
            "agent_lifecycle_mode": lifecycle_policy["mode"],
            "paid_providers_used": [],
            "ready_to_publish": False,
        },
        "candidates": normalized,
        "rejected_candidates": rejected_candidates[:100],
        "source_freshness": source_freshness,
        "attempts": [*tiktok_attempts, *mob_attempts, lifecycle_attempt],
        "limits": {
            "tiktok_max_creator_profiles": len(profiles),
            "tiktok_max_per_known_profile": 8,
            "tiktok_date_filtering": "new_d1_sources_since_created_at; legacy_profiles_unchanged",
            "tiktok_max_results": max_tiktok_results,
            "mob_x_max_candidates_per_run": 12,
            "mob_official_source_lanes": ["x"],
            "mob_x_source_handle": "@mobgamesstudios",
            "mob_x_api_credits_used": 0,
            "mob_birth_cutoff_active_only_in_production_mode": True,
            "media_downloads": 0,
            "rubika_posts": 0,
        },
    }
    _atomic_json_write(Path(output_path), report)
    return report

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    try:
        report = collect_daily_report(load_remote_sources=True)
    except Exception as exc:
        print(f"Discovery failed safely: {type(exc).__name__}", file=sys.stderr, flush=True)
        return 2

    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True), flush=True)
    print(f"Metadata report saved: {REPORT_PATH}", flush=True)
    print("Done: metadata only. No media downloaded and nothing published to Rubika.", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
