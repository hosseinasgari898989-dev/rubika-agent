"""Canonical identity helpers shared by automatic and owner-linked Mob X lanes."""
from __future__ import annotations

import re
from typing import Any


_POST_ID = re.compile(r"^[0-9]{15,22}$")


def normalize_mob_x_external_id(value: Any) -> str | None:
    """Return the numeric post ID from raw, x-prefixed, or D1-qualified identities."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.startswith("mob_official:"):
        normalized = normalized[len("mob_official:"):]
    if normalized.startswith("x-"):
        normalized = normalized[2:]
    return normalized if _POST_ID.fullmatch(normalized) else None


def mob_x_identity_aliases(value: Any) -> set[str]:
    """Return both historical external-ID forms and their D1 item identities."""
    post_id = normalize_mob_x_external_id(value)
    if post_id is None:
        return set()
    return {
        post_id,
        "x-" + post_id,
        "mob_official:" + post_id,
        "mob_official:x-" + post_id,
    }
