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
from agent.brain.cloud_state import CloudState
from agent.source_policy import refresh_dynamic_tiktok_sources, set_dynamic_tiktok_sources


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "artifacts" / "termux_discovery.json"

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

    if load_remote_sources:
        try:
            token = os.getenv("AGENT_API_TOKEN", "").strip()
            if len(token) < 32:
                raise RuntimeError("console_sources_auth_unavailable")
            cloud = CloudState(
                os.getenv("AGENT_API_BASE_URL", "https://rubika-agent.h98609294.workers.dev"),
                token=token, timeout_seconds=25,
            )
            source_rows, source_rows_available = refresh_dynamic_tiktok_sources(cloud)
            if not source_rows_available:
                raise RuntimeError("console_sources_unavailable")
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
        except Exception:
            # The optional overlay must never block either public discovery lane.
            # Do not log exception text: it can include request or transport details.
            set_dynamic_tiktok_sources([])
            dynamic_cutoffs = {}
            profiles = TIKTOK_PROFILES
            source_rows_available = False
            source_status = "console_sources_unavailable_using_legacy_six"
            source_diagnostics.append({
                "source": "tiktok_console_sources",
                "reason": "console_sources_unavailable_using_legacy_six",
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
                continue
            item_id = row.get("id") or row.get("external_id")
            url = row.get("url") or row.get("canonical_url")
            if not isinstance(item_id, str) or not item_id or not isinstance(url, str):
                continue
            if platform == "tiktok" and not (isinstance(row.get("source_policy"), dict) and row["source_policy"].get("known_source") is True):
                continue
            if item_id in seen:
                continue
            seen.add(item_id)
            normalized[platform].append(row)

    now = datetime.now(timezone.utc)
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
            "tiktok_account_removals_applied": source_rows_available if load_remote_sources else None,
            "tiktok_missing_created_at_cutoff_count": len(missing_cutoff_attempts),
            "agent_lifecycle_mode": lifecycle_policy["mode"],
            "paid_providers_used": [],
            "ready_to_publish": False,
        },
        "candidates": normalized,
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
