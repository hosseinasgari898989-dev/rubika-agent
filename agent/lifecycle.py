"""Lifecycle boundary for separating test runs from the eventual production launch.

During test mode, historical public candidates are allowed so the pipeline can be
validated. Before production activation, set config/agent_lifecycle.json to mode
"production" and record the agreed UTC birth timestamp. Production then rejects any
candidate without a verifiable publication timestamp or posted before that boundary.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = ROOT / "config" / "agent_lifecycle.json"


class LifecyclePolicyError(ValueError):
    """Lifecycle configuration is missing or unsafe to interpret."""


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def load_lifecycle_policy(path: str | Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecyclePolicyError("lifecycle_policy_unavailable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise LifecyclePolicyError("lifecycle_policy_invalid")
    mode = payload.get("mode")
    if mode not in {"test", "production"}:
        raise LifecyclePolicyError("lifecycle_mode_invalid")
    birth_value = payload.get("born_at")
    if mode == "production" and _parse_utc(birth_value) is None:
        raise LifecyclePolicyError("production_birth_timestamp_missing_or_invalid")
    return {
        "schema_version": 1,
        "mode": mode,
        "born_at": birth_value,
        "birth_datetime": _parse_utc(birth_value),
    }


def candidate_passes_birth_cutoff(
    candidate: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    active = policy if policy is not None else load_lifecycle_policy()
    if active.get("mode") == "test":
        return True, "test_mode_no_birth_cutoff"

    born_at = active.get("birth_datetime")
    if not isinstance(born_at, datetime):
        born_at = _parse_utc(active.get("born_at"))
    if not isinstance(born_at, datetime):
        return False, "production_birth_timestamp_missing_or_invalid"

    published = _parse_utc(candidate.get("published_at"))
    if published is None:
        return False, "publication_time_unverified"
    if published < born_at:
        return False, "published_before_agent_birth"
    return True, "published_at_or_after_agent_birth"


def filter_candidates_by_birth(
    candidates: list[dict[str, Any]],
    *,
    policy: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    active = policy if policy is not None else load_lifecycle_policy()
    allowed: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        passes, reason = candidate_passes_birth_cutoff(candidate, policy=active)
        if passes:
            allowed.append(candidate)
        else:
            rejected.append({
                "id": str(candidate.get("external_id") or candidate.get("id") or "")[:120],
                "reason": reason,
            })
    return allowed, rejected
