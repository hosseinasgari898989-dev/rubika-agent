"""Shared delivery-aware deduplication rules.

A historical row is not automatically a duplicate delivery. Discovered, ready, and
definitely-failed rows remain recoverable. Sending, sent, and uncertain rows block
new sends; legacy records without a status are conservatively protected.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

BLOCKING_DELIVERY_STATUSES = frozenset({"sending", "sent", "uncertain"})


def status_blocks_delivery(status: Any) -> bool:
    """Whether a stored status must block another delivery attempt."""
    if not isinstance(status, str) or not status.strip():
        return True
    return status.strip().casefold() in BLOCKING_DELIVERY_STATUSES


def blocking_ledger_entries(items: Any) -> dict[str, Mapping[str, Any]]:
    """Return only entries whose delivery state must block an automatic retry."""
    if not isinstance(items, Mapping):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for raw_key, item in items.items():
        if not isinstance(raw_key, str) or not isinstance(item, Mapping):
            continue
        if status_blocks_delivery(item.get("status")):
            result[raw_key] = item
    return result


def blocking_ledger_hashes(items: Any) -> set[str]:
    """Return valid media hashes from sent/uncertain/sending (or legacy) items only."""
    result: set[str] = set()
    for item in blocking_ledger_entries(items).values():
        digest = item.get("sha256")
        if isinstance(digest, str) and len(digest) == 64:
            lowered = digest.casefold()
            if all(char in "0123456789abcdef" for char in lowered):
                result.add(lowered)
    return result
