"""Canonical identity helpers for Mob X posts shared by automatic and manual paths."""

from __future__ import annotations

import re

_POST_ID_RE = re.compile(r"^(?:x-)?([0-9]{15,22})$", re.IGNORECASE)


def canonical_mob_x_external_id(value: object) -> str:
    """Return the one D1 external_id form for an official Mob X post."""
    match = _POST_ID_RE.fullmatch(str(value or "").strip())
    if not match:
        raise ValueError("invalid_mob_x_external_id")
    return "x-" + match.group(1)


def canonical_mob_x_item_id(value: object) -> str:
    """Return the canonical items.id used by automatic and manual ingestion."""
    return "mob_official:" + canonical_mob_x_external_id(value)


def mob_x_item_id_aliases(value: object) -> tuple[str, str]:
    """Return (canonical, legacy) D1 IDs for duplicate detection."""
    canonical_external_id = canonical_mob_x_external_id(value)
    numeric_id = canonical_external_id[2:]
    return (
        "mob_official:" + canonical_external_id,
        "mob_official:" + numeric_id,
    )
