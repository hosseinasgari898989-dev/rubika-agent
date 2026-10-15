"""Termux metadata collector imported by the scheduled GitHub Actions cloud runner.

The collector is run inside Termux Docker by the cloud workflow. It does not provide
a phone-side daily command or push its own code/report; the workflow validates and syncs
the report. It only gathers public metadata and never downloads or publishes media.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.discovery.mob_official_complete import discover_all_official_posts
from agent.discovery.termux_sources import discover_tiktok_hashtags
from agent.lifecycle import filter_candidates_by_birth, load_lifecycle_policy


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


def collect_daily_report(*, output_path: str | Path = REPORT_PATH) -> dict[str, Any]:
    """Collect TikTok edit metadata and official Mob X posts without downloads or publishing."""
    print("1/2 TikTok: checking owner-selected profiles and public hashtag pages (metadata only)...", flush=True)
    tiktok_candidates, tiktok_attempts = discover_tiktok_hashtags(
        include_unknown_sources=True,
        # Keep enough metadata candidates per creator to replenish the next batch
        # when recent IDs are already in D1; still metadata-only, never media bytes.
        max_results=60,
        known_source_max_results=48,
        unknown_source_max_results=12,
        max_per_profile=8,
        max_age_days=90,
    )
    print("2/2 Mob Entertainment: checking official newsroom, YouTube, Steam and X (metadata only)...", flush=True)
    mob_candidates, mob_attempts = discover_all_official_posts(max_results=12)

    lifecycle_policy = load_lifecycle_policy()
    tiktok_before = len(tiktok_candidates)
    mob_before = len(mob_candidates)
    tiktok_candidates, stale_tiktok = filter_candidates_by_birth(
        tiktok_candidates, policy=lifecycle_policy
    )
    mob_candidates, stale_mob = filter_candidates_by_birth(
        mob_candidates, policy=lifecycle_policy
    )
    lifecycle_attempt = {
        "source": "agent_lifecycle",
        "mode": lifecycle_policy["mode"],
        "birth_cutoff_enabled": lifecycle_policy["mode"] == "production",
        "tiktok_rejected": len(stale_tiktok),
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
            if item_id in seen:
                continue
            seen.add(item_id)
            normalized[platform].append(row)

    now = datetime.now(timezone.utc)
    report = {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "source": "termux_phone",
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
            "mob_official_method": "official_newsroom_with_youtube_steam_and_x_fallbacks",
            "mob_official_birth_cutoff_rejected": len(stale_mob),
            "tiktok_birth_cutoff_rejected": len(stale_tiktok),
            "tiktok_known_source_candidates": sum(
                1 for row in normalized["tiktok"]
                if isinstance(row.get("source_policy"), dict)
                and row["source_policy"].get("known_source") is True
            ),
            "tiktok_unknown_source_candidates": sum(
                1 for row in normalized["tiktok"]
                if not (
                    isinstance(row.get("source_policy"), dict)
                    and row["source_policy"].get("known_source") is True
                )
            ),
            "tiktok_method": "yt_dlp_owner_selected_profiles_plus_public_hashtag_pages",
            "agent_lifecycle_mode": lifecycle_policy["mode"],
            "paid_providers_used": [],
            "ready_to_publish": False,
        },
        "candidates": normalized,
        "attempts": [*tiktok_attempts, *mob_attempts, lifecycle_attempt],
        "limits": {
            "tiktok_max_creator_profiles": 6,
            "tiktok_max_per_known_profile": 8,
            "tiktok_unknown_source_max_candidates": 12,
            "tiktok_max_age_days": 90,
            "tiktok_max_results": 60,
            "mob_x_max_candidates_per_run": 12,
            "mob_official_source_lanes": ["website", "youtube", "steam", "x"],
            "mob_x_source_handle": "@mobgamesstudios",
            "mob_x_api_credits_used": 0,
            "birth_cutoff_active_only_in_production_mode": True,
            "media_downloads": 0,
            "rubika_posts": 0,
        },
    }
    _atomic_json_write(Path(output_path), report)
    return report
