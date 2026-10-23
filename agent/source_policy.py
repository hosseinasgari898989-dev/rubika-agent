"""Fail-closed TikTok source configuration for the six owner-selected channels."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "tiktok_source_policy.json"
DEFAULT_REQUIRED_ANY_KEYWORDS = ("poppy", "playtime")
DEFAULT_DOWNLOAD_QUALITY_MAX = 1080
DEFAULT_ALLOW_UPSCALE = False


@lru_cache(maxsize=1)
def load_source_policy_config() -> dict[str, Any]:
    """Load repository policy; invalid configuration fails closed for source access."""
    try:
        value = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict) or value.get("version") != 1:
        return {
            "version": 1,
            "delivery": {"youtube_to_rubika_enabled": False, "tiktok_to_rubika_enabled": False},
            "discovery": {},
            "required_any_keywords": list(DEFAULT_REQUIRED_ANY_KEYWORDS),
            "download_quality_max": DEFAULT_DOWNLOAD_QUALITY_MAX,
            "allow_upscale": DEFAULT_ALLOW_UPSCALE,
            "sources": [],
        }
    return value


def known_tiktok_profiles() -> tuple[str, ...]:
    rows = load_source_policy_config().get("sources", [])
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


def required_any_keywords() -> tuple[str, ...]:
    values = load_source_policy_config().get("required_any_keywords")
    if not isinstance(values, list):
        return DEFAULT_REQUIRED_ANY_KEYWORDS
    cleaned = tuple(value.strip().casefold() for value in values if isinstance(value, str) and value.strip())
    return cleaned or DEFAULT_REQUIRED_ANY_KEYWORDS


def has_required_any_keyword(caption: str, hashtags: Any = None) -> bool:
    """Match configured case-insensitive substrings in caption and hashtags only."""
    parts = [caption] if isinstance(caption, str) else []
    if isinstance(hashtags, str):
        parts.append(hashtags)
    elif isinstance(hashtags, (list, tuple)):
        parts.extend(value for value in hashtags if isinstance(value, str))
    text = " ".join(parts).casefold()
    return any(keyword in text for keyword in required_any_keywords())


def download_quality_max() -> int:
    value = load_source_policy_config().get("download_quality_max", DEFAULT_DOWNLOAD_QUALITY_MAX)
    return value if type(value) is int and value in {360, 480, 720, 1080, 1440, 2160} else DEFAULT_DOWNLOAD_QUALITY_MAX


def allow_upscale() -> bool:
    return load_source_policy_config().get("allow_upscale", DEFAULT_ALLOW_UPSCALE) is True


def resolve_source_policy(platform: str, source_key: str) -> dict[str, Any]:
    """Resolve a configured source. Unlisted TikTok handles are ineligible for processing."""
    handle = str(source_key or "").strip().lstrip("@")
    rows = load_source_policy_config().get("sources", [])
    if platform == "tiktok" and isinstance(rows, list):
        for row in rows:
            if (
                isinstance(row, dict)
                and row.get("platform") == "tiktok"
                and isinstance(row.get("handle"), str)
                and row.get("handle").strip().lstrip("@").casefold() == handle.casefold()
                and row.get("enabled", True) is True
            ):
                result = dict(row)
                result["known_source"] = True
                result["enabled"] = True
                result["source_handle"] = row["handle"].strip().lstrip("@")
                return result
    if platform != "tiktok":
        # Keep existing non-TikTok staging contracts compatible; TikTok unknown
        # sources are the only path removed by the owner-selected source policy.
        return {
            "known_source": False,
            "enabled": True,
            "trust_tier": "unknown",
            "source_handle": handle,
            "minimum_source_short_side": 480,
            "output_format": "square_1080x1080",
            "require_poppy_relevance_check": False,
            "require_edit_signal_check": False,
        }
    return {
        "known_source": False,
        "enabled": False,
        "trust_tier": "unselected",
        "source_handle": handle,
        "minimum_source_short_side": 0,
        "output_format": None,
        "require_poppy_relevance_check": False,
        "require_edit_signal_check": False,
    }


def delivery_enabled(platform: str) -> bool:
    """Return whether a platform is allowed to send media to Rubika."""
    delivery = load_source_policy_config().get("delivery", {})
    return isinstance(delivery, dict) and delivery.get(f"{platform}_to_rubika_enabled") is True
