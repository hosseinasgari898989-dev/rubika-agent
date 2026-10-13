"""Fail-closed reuse permission registry for source-level and item-level approvals."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import Candidate


PERMISSION_BASES = frozenset({"owner", "explicit_permission", "license"})
PERMITTED_PLATFORMS = frozenset({"youtube", "tiktok", "instagram", "mob_official"})


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str
    basis: str | None = None


class SourceRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class SourcePermission:
    platform: str
    source_key: str
    permission_basis: str
    permission_confirmed: bool
    enabled: bool
    notes: str = ""


@dataclass(frozen=True)
class ItemPermission:
    platform: str
    external_id: str
    permission_basis: str
    permission_confirmed: bool
    enabled: bool


class SourceRegistry:
    """Registry is opt-in: an empty/missing registry authorizes no source or item."""

    def __init__(
        self,
        sources: tuple[SourcePermission, ...] = (),
        items: tuple[ItemPermission, ...] = (),
    ) -> None:
        self.sources = sources
        self.items = items

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SourceRegistry":
        if payload.get("version") != 1:
            raise SourceRegistryError("unsupported_registry_version")
        raw_sources = payload.get("sources", [])
        raw_items = payload.get("items", [])
        if not isinstance(raw_sources, list) or not isinstance(raw_items, list):
            raise SourceRegistryError("registry_lists_required")

        sources: list[SourcePermission] = []
        seen_sources: set[tuple[str, str]] = set()
        for row in raw_sources:
            if not isinstance(row, dict):
                raise SourceRegistryError("source_entry_must_be_object")
            platform = row.get("platform")
            key = row.get("source_key")
            basis = row.get("permission_basis")
            confirmed = row.get("permission_confirmed")
            enabled = row.get("enabled", False)
            notes = row.get("notes", "")
            if platform not in PERMITTED_PLATFORMS:
                raise SourceRegistryError("source_platform_invalid")
            if not isinstance(key, str) or not key.strip() or len(key) > 200:
                raise SourceRegistryError("source_key_invalid")
            if basis not in PERMISSION_BASES or type(confirmed) is not bool or type(enabled) is not bool:
                raise SourceRegistryError("source_permission_fields_invalid")
            identity = (platform, key.strip())
            if identity in seen_sources:
                raise SourceRegistryError("duplicate_source_permission")
            seen_sources.add(identity)
            sources.append(SourcePermission(
                platform=platform,
                source_key=key.strip(),
                permission_basis=basis,
                permission_confirmed=confirmed,
                enabled=enabled,
                notes=notes.strip()[:300] if isinstance(notes, str) else "",
            ))

        items: list[ItemPermission] = []
        seen_items: set[tuple[str, str]] = set()
        for row in raw_items:
            if not isinstance(row, dict):
                raise SourceRegistryError("item_entry_must_be_object")
            platform = row.get("platform")
            external_id = row.get("external_id")
            basis = row.get("permission_basis")
            confirmed = row.get("permission_confirmed")
            enabled = row.get("enabled", False)
            if platform not in PERMITTED_PLATFORMS:
                raise SourceRegistryError("item_platform_invalid")
            if not isinstance(external_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", external_id):
                raise SourceRegistryError("item_external_id_invalid")
            if basis not in PERMISSION_BASES or type(confirmed) is not bool or type(enabled) is not bool:
                raise SourceRegistryError("item_permission_fields_invalid")
            identity = (platform, external_id)
            if identity in seen_items:
                raise SourceRegistryError("duplicate_item_permission")
            seen_items.add(identity)
            items.append(ItemPermission(
                platform=platform,
                external_id=external_id,
                permission_basis=basis,
                permission_confirmed=confirmed,
                enabled=enabled,
            ))

        return cls(tuple(sources), tuple(items))

    @classmethod
    def load(cls, path: str | Path) -> "SourceRegistry":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError):
            raise SourceRegistryError("registry_unreadable") from None
        if not isinstance(payload, dict):
            raise SourceRegistryError("registry_root_must_be_object")
        return cls.from_mapping(payload)

    def check(self, candidate: Candidate) -> PermissionDecision:
        # Per-item approval has precedence only if it is valid and enabled.
        for item in self.items:
            if item.platform == candidate.platform and item.external_id == candidate.external_id:
                if item.enabled and item.permission_confirmed and item.permission_basis in PERMISSION_BASES:
                    return PermissionDecision(True, "explicit_item_permission", item.permission_basis)
                return PermissionDecision(False, "item_permission_not_confirmed")

        for source in self.sources:
            if source.platform == candidate.platform and source.source_key == candidate.source_key:
                if source.enabled and source.permission_confirmed and source.permission_basis in PERMISSION_BASES:
                    return PermissionDecision(True, "explicit_source_permission", source.permission_basis)
                return PermissionDecision(False, "source_permission_not_confirmed")
        return PermissionDecision(False, "source_not_authorized")

    def authorized_source_keys(self, platform: str | None = None) -> set[str]:
        return {
            row.source_key for row in self.sources
            if row.enabled and row.permission_confirmed
            and row.permission_basis in PERMISSION_BASES
            and (platform is None or row.platform == platform)
        }
