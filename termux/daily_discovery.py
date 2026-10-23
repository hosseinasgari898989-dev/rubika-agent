"""Metadata-only collector run inside Termux Docker by GitHub Actions.

The hosted workflow owns report commits and queue intake. This module does not push
to GitHub, download media, use paid AI providers, or publish to Rubika.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.discovery.mob_official_complete import discover_official_x_posts
from agent.discovery.termux_sources import discover_tiktok_profiles
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
    print("1/2 TikTok: checking the six selected creator profiles (metadata only)...", flush=True)
    tiktok_candidates, tiktok_attempts = discover_tiktok_profiles(
        max_results=48,
        max_per_profile=8,
    )
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
            "agent_lifecycle_mode": lifecycle_policy["mode"],
            "paid_providers_used": [],
            "ready_to_publish": False,
        },
        "candidates": normalized,
        "attempts": [*tiktok_attempts, *mob_attempts, lifecycle_attempt],
        "limits": {
            "tiktok_max_creator_profiles": 6,
            "tiktok_max_per_known_profile": 8,
            "tiktok_date_filtering": "deferred_to_phase_6_since_utc_engine",
            "tiktok_max_results": 60,
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
        report = collect_daily_report()
    except Exception as exc:
        print(f"Discovery failed safely: {type(exc).__name__}", file=sys.stderr, flush=True)
        return 2

    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True), flush=True)
    print(f"Metadata report saved: {REPORT_PATH}", flush=True)
    print("Done: metadata only. No media downloaded and nothing published to Rubika.", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
