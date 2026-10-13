"""Shared, fail-closed creator and delivery policy for Rubika-Agent."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "tiktok_source_policy.json"

_SAFE_UNKNOWN_POLICY: dict[str, Any] = {
    "trust_tier": "unknown",
    "known_source": False,
    "output_format": "square_1080x1080",
    "require_visual_poppy_check": True,
    "require_visual_edit_check": True,
    "require_ai_quality_check": True,
    "minimum_source_short_side": 720,
    "notes": "Unknown source: require full verification before publication.",
}


@lru_cache(maxsize=1)
def load_source_policy_config() -> dict[str, Any]:
    """Load the repository policy. Unsafe/missing configuration fails closed."""
    try:
        value = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "version": 1,
            "delivery": {"youtube_to_rubika_enabled": False},
            "discovery": {
                "tiktok_max_results": 36,
                "known_source_max_results": 24,
                "known_source_max_per_profile": 4,
                "unknown_source_max_results": 12,
                "tiktok_max_age_days": 90,
            },
            "sources": [],
            "unknown_source_defaults": dict(_SAFE_UNKNOWN_POLICY),
        }
    if not isinstance(value, dict) or value.get("version") != 1:
        return {
            "version": 1,
            "delivery": {"youtube_to_rubika_enabled": False},
            "discovery": {},
            "sources": [],
            "unknown_source_defaults": dict(_SAFE_UNKNOWN_POLICY),
        }
    return value


def known_tiktok_profiles() -> tuple[str, ...]:
    config = load_source_policy_config()
    rows = config.get("sources", [])
    if not isinstance(rows, list):
        return ()
    return tuple(
        str(row["handle"]).strip().lstrip("@")
        for row in rows
        if isinstance(row, dict)
        and row.get("platform") == "tiktok"
        and row.get("enabled", True) is True
        and isinstance(row.get("handle"), str)
        and row.get("handle").strip()
    )


def resolve_source_policy(platform: str, source_key: str) -> dict[str, Any]:
    """Resolve policy by canonical creator handle; never trust report-supplied rules."""
    handle = str(source_key or "").strip().lstrip("@").casefold()
    config = load_source_policy_config()
    sources = config.get("sources", [])
    if platform == "tiktok" and isinstance(sources, list):
        for row in sources:
            if (
                isinstance(row, dict)
                and row.get("platform") == platform
                and isinstance(row.get("handle"), str)
                and row.get("handle").strip().lstrip("@").casefold() == handle
                and row.get("enabled", True) is True
            ):
                result = dict(row)
                result["known_source"] = True
                result["source_handle"] = row["handle"].strip().lstrip("@")
                return result
    defaults = config.get("unknown_source_defaults", {})
    result = dict(_SAFE_UNKNOWN_POLICY)
    if isinstance(defaults, dict):
        result.update(defaults)
    result["known_source"] = False
    result["source_handle"] = handle
    result["trust_tier"] = "unknown"
    return result


def delivery_enabled(platform: str) -> bool:
    """Return whether a platform is allowed to send media to Rubika."""
    config = load_source_policy_config()
    delivery = config.get("delivery", {})
    if not isinstance(delivery, dict):
        return False
    return delivery.get(f"{platform}_to_rubika_enabled") is True
