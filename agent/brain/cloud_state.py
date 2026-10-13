"""Authenticated client for the Rubika-Agent D1-backed control Worker."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Mapping


class CloudStateError(RuntimeError):
    """Safe, non-sensitive API error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class CloudState:
    """Remote run/item/event state. No token is embedded or logged."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        timeout_seconds: int = 15,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("control_api_must_use_https")
        self.base_url = base_url.rstrip("/")
        self.token = (token if token is not None else os.getenv("AGENT_API_TOKEN", "")).strip()
        self.timeout_seconds = timeout_seconds
        if len(self.token) < 32:
            raise ValueError("missing_or_short_AGENT_API_TOKEN")

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/api/brain/"):
            raise ValueError("path_outside_brain_api")
        if method not in {"GET", "POST"}:
            raise ValueError("unsupported_method")
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(body) > 32 * 1024:
                raise ValueError("request_too_large")
        else:
            body = None

        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.token,
                "User-Agent": "rubika-agent-brain/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(256 * 1024).decode("utf-8")
                result = json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise CloudStateError("control_api_http_" + str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise CloudStateError("control_api_unreachable") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CloudStateError("control_api_invalid_json") from None

        if not isinstance(result, dict) or result.get("ok") is not True:
            code = result.get("error", "control_api_rejected") if isinstance(result, dict) else "control_api_invalid_response"
            safe = re.sub(r"[^a-zA-Z0-9_-]", "", str(code).lower())[:60] or "rejected"
            raise CloudStateError("control_api_" + safe)
        return result

    def create_run(self, trigger_type: str = "manual", run_id: str | None = None) -> str:
        identifier = run_id or str(uuid.uuid4())
        self._request("POST", "/api/brain/runs", {
            "id": identifier,
            "trigger_type": trigger_type,
        })
        return identifier

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        found_count: int = 0,
        eligible_count: int = 0,
        sent_count: int = 0,
        skipped_count: int = 0,
        failed_count: int = 0,
        summary: str = "",
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", run_id):
            raise ValueError("invalid_run_id")
        return self._request("POST", f"/api/brain/runs/{run_id}/finish", {
            "status": status,
            "found_count": found_count,
            "eligible_count": eligible_count,
            "sent_count": sent_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "summary": summary[:2000],
        })

    def upsert_item(self, item: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/brain/items/upsert", item)

    def upsert_staged(self, staged: Any) -> dict[str, Any]:
        """Persist a StagedMedia record without approving it or publishing it."""
        candidate = staged.candidate
        inspection = staged.inspection
        evaluation = staged.evaluation
        validation = {
            "ok": bool(inspection.ok),
            "media_type": inspection.media_type,
            "sha256": inspection.sha256,
            "size_bytes": inspection.size_bytes,
            "mime_type": inspection.mime_type,
            "width": inspection.width,
            "height": inspection.height,
            "duration_seconds": inspection.duration_seconds,
            "has_audio": inspection.has_audio,
            "video_codec": inspection.video_codec,
            "audio_codec": inspection.audio_codec,
            "errors": list(inspection.errors),
            "warnings": list(inspection.warnings),
        }
        moderation_names = (
            "poppy_relevant", "is_edit", "insults_poppy",
            "insults_other_games", "high_effort", "has_music",
        )
        moderation = {
            key: evaluation.checks[key]
            for key in moderation_names
            if isinstance(evaluation.checks.get(key), bool)
        }
        payload = {
            "platform": candidate.platform,
            "source_key": candidate.source_key or candidate.platform + "_search",
            "external_id": candidate.external_id,
            "canonical_url": candidate.canonical_url,
            "title": candidate.title,
            "caption": staged.caption or candidate.caption,
            "published_at": candidate.published_at,
            "status": "ready",
            "review_state": "pending",
            "media_type": inspection.media_type,
            "source_tier": candidate.source_tier,
            "storage_key": staged.storage_key,
            "media_sha256": inspection.sha256,
            "media_bytes": inspection.size_bytes,
            "media_mime": inspection.mime_type,
            "validation": validation,
            "moderation": moderation or None,
            "original_caption": candidate.original_caption,
            "persian_translation": candidate.persian_translation,
        }
        return self.upsert_item(payload)

    def record_attempt(
        self,
        item_id: str,
        attempt_number: int,
        status: str,
        *,
        error_code: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,20}:[A-Za-z0-9._-]{1,120}", item_id):
            raise ValueError("invalid_item_id")
        escaped = urllib.parse.quote(item_id, safe=":")
        return self._request("POST", f"/api/brain/items/{escaped}/attempts", {
            "attempt_number": attempt_number,
            "status": status,
            "error_code": error_code,
            "rubika_message_id": message_id,
        })

    def update_storage(
        self,
        item_id: str,
        storage_key: str | None,
        event: str,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,20}:[A-Za-z0-9._-]{1,120}", item_id):
            raise ValueError("invalid_item_id")
        escaped = urllib.parse.quote(item_id, safe=":")
        return self._request("POST", f"/api/brain/items/{escaped}/storage", {
            "storage_key": storage_key,
            "event": event,
        })

    def add_event(
        self,
        code: str,
        *,
        level: str = "info",
        run_id: str | None = None,
        item_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", "/api/brain/events", {
            "code": code,
            "level": level,
            "run_id": run_id,
            "item_id": item_id,
            "details": dict(details or {}),
        })

    def get_approved_queue(self, limit: int = 25) -> list[dict[str, Any]]:
        bounded = min(100, max(1, int(limit)))
        response = self._request("GET", "/api/brain/queue?limit=" + str(bounded))
        items = response.get("items")
        return items if isinstance(items, list) else []
