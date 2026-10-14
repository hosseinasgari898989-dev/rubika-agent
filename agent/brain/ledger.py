"""Versioned durable JSON ledger stored inside the MEGA Rubika-Agent folder.

The ledger is for IDs, hashes, delivery status and history only; media bytes remain
in staging storage and are deleted only after a confirmed successful delivery.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from agent.storage.mega import MegaStorage, MegaStorageError, sha256_file
from .deduplication import status_blocks_delivery


STATE_SCHEMA_VERSION = 1
SNAPSHOT_RE = re.compile(r"^agent-ledger-(\d{8}T\d{12}Z)-([a-f0-9]{8})\.json$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "updated_at": _utc_now(),
        "items": {},
        "runs": [],
        "events": [],
    }


class MegaLedger:
    """Read/verify versioned MEGA snapshots and prune old versions after a safe save."""

    def __init__(
        self,
        storage: MegaStorage,
        *,
        state_directory: str = "state",
        keep_versions: int = 5,
    ) -> None:
        if keep_versions < 2:
            raise ValueError("keep_at_least_two_ledger_versions")
        self.storage = storage
        self.state_directory = state_directory
        self.keep_versions = keep_versions

    def _snapshot_names(self) -> list[str]:
        names = self.storage.list_directory(self.state_directory)
        return sorted(
            name for name in names
            if SNAPSHOT_RE.fullmatch(name)
        )

    def load(self) -> dict[str, Any]:
        names = self._snapshot_names()
        if not names:
            return new_state()

        # The latest snapshot is tried first; a corrupt newest version does not
        # destroy the previous known-good snapshot.
        with tempfile.TemporaryDirectory(prefix="rubika-ledger-load-") as temp:
            for name in reversed(names):
                remote = self.storage.root.rstrip("/") + "/" + self.state_directory.strip("/") + "/" + name
                try:
                    local = self.storage.download(remote, temp)
                    if local.stat().st_size > 10 * 1024 * 1024:
                        continue
                    payload = json.loads(local.read_text(encoding="utf-8"))
                    if (
                        isinstance(payload, dict)
                        and payload.get("schema_version") == STATE_SCHEMA_VERSION
                        and isinstance(payload.get("items"), dict)
                        and isinstance(payload.get("runs"), list)
                        and isinstance(payload.get("events"), list)
                    ):
                        return payload
                except (OSError, ValueError, json.JSONDecodeError, MegaStorageError):
                    continue
        raise MegaStorageError("no_valid_ledger_snapshot")

    def save(self, state: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(state, Mapping):
            raise ValueError("ledger_state_must_be_object")
        snapshot = copy.deepcopy(dict(state))
        if snapshot.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ValueError("unsupported_ledger_schema")
        if not isinstance(snapshot.get("items"), dict):
            raise ValueError("ledger_items_must_be_object")
        if not isinstance(snapshot.get("runs"), list) or not isinstance(snapshot.get("events"), list):
            raise ValueError("ledger_history_must_be_lists")
        snapshot["updated_at"] = _utc_now()

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        name = f"agent-ledger-{timestamp}-{uuid.uuid4().hex[:8]}.json"
        with tempfile.TemporaryDirectory(prefix="rubika-ledger-save-") as temp:
            local = Path(temp) / name
            local.write_text(
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            expected_hash = sha256_file(local)
            remote = self.storage.upload(local, self.state_directory)
            verified = self.storage.download(remote, Path(temp) / "verify")
            actual_hash = sha256_file(verified)
            if expected_hash != actual_hash:
                raise MegaStorageError("ledger_roundtrip_hash_mismatch")

        names = self._snapshot_names()
        older = names[:-self.keep_versions]
        cleanup_pending = []
        for old_name in older:
            old_remote = self.storage.root.rstrip("/") + "/" + self.state_directory.strip("/") + "/" + old_name
            try:
                self.storage.delete(old_remote)
            except (OSError, ValueError, MegaStorageError):
                cleanup_pending.append(old_name)

        return {
            "ok": True,
            "remote_path": remote,
            "sha256": expected_hash,
            "cleanup_pending": cleanup_pending,
        }

    @staticmethod
    def start_run(state: dict[str, Any], run_id: str, trigger_type: str = "manual") -> None:
        if not run_id or "/" in run_id or ".." in run_id:
            raise ValueError("invalid_run_id")
        runs = state.setdefault("runs", [])
        if not isinstance(runs, list):
            raise ValueError("ledger_runs_must_be_list")
        if any(isinstance(run, Mapping) and run.get("id") == run_id for run in runs):
            return
        runs.append({
            "id": run_id,
            "trigger_type": trigger_type[:40],
            "status": "running",
            "started_at": _utc_now(),
            "finished_at": None,
            "found_count": 0,
            "eligible_count": 0,
            "sent_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
        })

    @staticmethod
    def finish_run(
        state: dict[str, Any],
        run_id: str,
        *,
        status: str,
        found_count: int = 0,
        eligible_count: int = 0,
        sent_count: int = 0,
        skipped_count: int = 0,
        failed_count: int = 0,
        summary: str = "",
    ) -> None:
        if status not in {"ok", "partial", "failed", "cancelled"}:
            raise ValueError("invalid_finished_run_status")
        runs = state.get("runs", [])
        run = next(
            (value for value in runs if isinstance(value, dict) and value.get("id") == run_id),
            None,
        )
        if run is None:
            raise ValueError("ledger_run_not_found")
        run.update({
            "status": status,
            "finished_at": _utc_now(),
            "found_count": max(0, int(found_count)),
            "eligible_count": max(0, int(eligible_count)),
            "sent_count": max(0, int(sent_count)),
            "skipped_count": max(0, int(skipped_count)),
            "failed_count": max(0, int(failed_count)),
            "summary": summary[:2000],
        })

    @staticmethod
    def is_duplicate(
        state: Mapping[str, Any],
        *,
        platform: str,
        external_id: str,
        sha256: str | None = None,
    ) -> bool:
        """Block only media whose delivery state makes a repeat send unsafe."""
        identity = platform + ":" + external_id
        items = state.get("items", {})
        if not isinstance(items, Mapping):
            return False
        for key, item in items.items():
            if not isinstance(item, Mapping):
                # Unknown legacy ledger entries are conservatively protected by identity.
                if key == identity:
                    return True
                continue
            if not status_blocks_delivery(item.get("status")):
                continue
            if key == identity:
                return True
            if item.get("platform") == platform and item.get("external_id") == external_id:
                return True
            if sha256 and item.get("sha256") == sha256:
                return True
        return False

    @staticmethod
    def upsert_item(state: dict[str, Any], item_id: str, values: Mapping[str, Any]) -> None:
        if not item_id or "/" in item_id or ".." in item_id:
            raise ValueError("invalid_item_id")
        items = state.setdefault("items", {})
        if not isinstance(items, dict):
            raise ValueError("ledger_items_must_be_object")
        previous = items.get(item_id, {})
        if not isinstance(previous, dict):
            previous = {}
        updated = dict(previous)
        old_status = updated.get("status")
        safe_values = {
            key: value for key, value in dict(values).items()
            if not any(marker in key.casefold() for marker in (
                "token", "password", "secret", "authorization", "cookie"
            ))
        }
        updated.update(safe_values)
        # A later discovery must not reset a confirmed post or an uncertain send.
        if old_status in {"sent", "uncertain"}:
            updated["status"] = old_status
        updated["updated_at"] = _utc_now()
        updated.setdefault("attempts", [])
        updated.setdefault("status", "discovered")
        items[item_id] = updated

    @staticmethod
    def record_attempt(
        state: dict[str, Any],
        item_id: str,
        *,
        attempt_number: int,
        status: str,
        error_code: str | None = None,
        message_id: str | None = None,
    ) -> None:
        if attempt_number < 1 or attempt_number > 3:
            raise ValueError("attempt_number_out_of_range")
        if status not in {"started", "sent", "failed", "uncertain"}:
            raise ValueError("invalid_attempt_status")
        item = state.get("items", {}).get(item_id)
        if not isinstance(item, dict):
            raise ValueError("ledger_item_not_found")
        attempts = item.setdefault("attempts", [])
        prior = next((record for record in attempts if record.get("attempt_number") == attempt_number), None)
        row = {
            "attempt_number": attempt_number,
            "status": status,
            "error_code": error_code,
            "message_id": message_id,
            "updated_at": _utc_now(),
        }
        if prior is None:
            attempts.append(row)
        else:
            prior.update(row)
        attempts.sort(key=lambda record: int(record.get("attempt_number", 0)))
        item["attempt_count"] = max(int(item.get("attempt_count", 0)), attempt_number)
        item["updated_at"] = _utc_now()
        if status == "sent":
            item["status"] = "sent"
            item["sent_at"] = _utc_now()
            item["rubika_message_id"] = message_id
        elif status == "uncertain":
            item["status"] = "uncertain"
        elif status == "failed":
            item["status"] = "failed" if attempt_number >= 3 else "queued"
        elif status == "started":
            item["status"] = "sending"

    @staticmethod
    def add_event(
        state: dict[str, Any],
        code: str,
        *,
        level: str = "info",
        item_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if level not in {"debug", "info", "warning", "error"}:
            raise ValueError("invalid_event_level")
        events = state.setdefault("events", [])
        if not isinstance(events, list):
            raise ValueError("ledger_events_must_be_list")
        # Avoid accidentally storing secrets or full provider error bodies.
        safe_code = "".join(ch for ch in code.lower() if ch.isalnum() or ch in "_-")[:80]
        events.append({
            "at": _utc_now(),
            "level": level,
            "code": safe_code or "event",
            "item_id": item_id,
            "details": dict(details or {}),
        })
