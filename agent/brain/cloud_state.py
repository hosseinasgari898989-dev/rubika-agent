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
        *,
        max_body_bytes: int = 32 * 1024,
    ) -> dict[str, Any]:
        if not path.startswith("/api/brain/"):
            raise ValueError("path_outside_brain_api")
        if method not in {"GET", "POST"}:
            raise ValueError("unsupported_method")
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(body) > max_body_bytes:
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

    def translate_official(self, source_text: str) -> str:
        """Translate an official news excerpt through the authenticated Cloudflare AI route."""
        if not isinstance(source_text, str) or not source_text.strip():
            raise CloudStateError("translation_text_required")
        if len(source_text) > 4500:
            raise CloudStateError("translation_text_too_long")
        result = self._request(
            "POST",
            "/api/brain/translate-official",
            {"text": source_text},
            max_body_bytes=20 * 1024,
        )
        translation = result.get("translation")
        if not isinstance(translation, str) or not translation.strip():
            raise CloudStateError("control_api_translation_missing")
        clean = re.sub(r"\\s+", " ", translation).strip()
        if not re.search(r"[\\u0600-\\u06FF]", clean):
            raise CloudStateError("control_api_translation_not_persian")
        return clean[:6500]

    def moderate_music(
        self,
        audio_data_url: str,
        *,
        platform: str,
    ) -> dict[str, Any]:
        """Request an audio-only soundtrack decision; no visual questions are sent."""
        if platform not in {"youtube", "tiktok", "instagram"}:
            raise ValueError("invalid_music_platform")
        if (
            not isinstance(audio_data_url, str)
            or not audio_data_url.startswith("data:audio/mpeg;base64,")
        ):
            raise ValueError("music_audio_data_url_required")
        if len(audio_data_url) > 11_300_000:
            raise ValueError("music_audio_size_invalid")
        payload = {"audio": audio_data_url, "platform": platform}
        result = self._request(
            "POST",
            "/api/brain/moderate-music",
            payload,
            max_body_bytes=12 * 1024 * 1024,
        )
        checks = result.get("checks")
        probabilities = result.get("probabilities")
        diagnostic = result.get("diagnostic")
        if not isinstance(checks, dict) or not isinstance(probabilities, dict):
            raise CloudStateError("control_api_music_checks_missing")

        probability = probabilities.get("has_music")
        if probability is not None and (
            type(probability) not in {int, float} or not 0 <= probability <= 1
        ):
            raise CloudStateError("control_api_music_probability_invalid")
        check = checks.get("has_music")
        if check is not None and type(check) is not bool:
            raise CloudStateError("control_api_music_check_invalid")
        raw_reason = diagnostic.get("has_music") if isinstance(diagnostic, dict) else None
        reason = re.sub(r"[^a-zA-Z0-9_-]", "", str(raw_reason or "music_diagnostic_missing").lower())[:80]
        output: dict[str, Any] = {
            "music_probability": float(probability) if probability is not None else None,
            "music_decision_reason": reason or "music_diagnostic_missing",
            "music_detection_method": "cloudflare_clef_omni_audio",
            "music_model": str(result.get("model", ""))[:100],
            "music_confidence": float(result.get("confidence", 0.0)),
            "music_check_uncertain": type(check) is not bool,
            "human_review_required": True,
            "auto_approved": False,
            "publishing": False,
        }
        if type(check) is bool:
            output["has_music"] = check
        return output

    # Compatibility shim for older Python callers. The endpoint now accepts audio
    # only; sending a video data URL is rejected instead of invoking visual checks.
    def moderate_video(
        self,
        video_data_url: str,
        *,
        platform: str,
        title: str = "",
        caption: str = "",
    ) -> dict[str, Any]:
        return self.moderate_music(
            video_data_url,
            platform=platform,
        )

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

    @staticmethod
    def prepare_staged_payload(staged: Any) -> dict[str, Any]:
        """Build the exact pending-review D1 payload without making a network call."""
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
        moderation_names = ("has_music", "is_poppy_playtime", "is_edit", "high_quality_edit")
        moderation = {
            key: evaluation.checks[key]
            for key in moderation_names
            if isinstance(evaluation.checks.get(key), bool)
        }
        for key in (
            "music_probability", "music_decision_reason", "music_detection_method",
            "music_model", "music_confidence", "music_check_uncertain", "recovery_diagnostics",
        ):
            value = evaluation.checks.get(key)
            if value is not None and (
                key == "music_probability" and type(value) in {int, float}
                or key == "music_confidence" and type(value) in {int, float}
                or key == "music_check_uncertain" and type(value) is bool
                or key in {"music_decision_reason", "music_detection_method", "music_model"} and isinstance(value, str)
                or key == "recovery_diagnostics" and isinstance(value, dict)
            ):
                moderation[key] = value
        return {
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

    def upsert_staged(self, staged: Any) -> dict[str, Any]:
        """Persist staged metadata without approving it or publishing it."""
        return self.upsert_item(self.prepare_staged_payload(staged))

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

    def get_seen_items(
        self,
        *,
        platform: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        bounded_limit = min(500, max(1, int(limit)))
        bounded_offset = min(1000000, max(0, int(offset)))
        query = f"?limit={bounded_limit}&offset={bounded_offset}"
        if platform is not None:
            if platform not in {"tiktok", "youtube", "instagram", "mob_official", "music"}:
                raise ValueError("invalid_platform_filter")
            query += "&platform=" + urllib.parse.quote(platform, safe="")
        return self._request("GET", "/api/brain/items/seen" + query)

    def get_seen_index(self, *, page_size: int = 500, max_pages: int = 100) -> dict[str, set[str]]:
        """Read persistent source IDs/hashes for dedupe. Never treats failed reads as an empty index."""
        page_size = min(500, max(1, int(page_size)))
        max_pages = min(1000, max(1, int(max_pages)))
        ids: set[str] = set()
        hashes: set[str] = set()
        offset = 0
        for _ in range(max_pages):
            page = self.get_seen_items(limit=page_size, offset=offset)
            items = page.get("items")
            if not isinstance(items, list):
                raise CloudStateError("control_api_invalid_seen_items")
            for item in items:
                if not isinstance(item, dict):
                    continue
                platform = item.get("platform")
                external_id = item.get("external_id")
                if isinstance(platform, str) and isinstance(external_id, str) and external_id:
                    ids.add(platform + ":" + external_id)
                digest = item.get("media_sha256")
                if isinstance(digest, str) and re.fullmatch(r"[a-fA-F0-9]{64}", digest):
                    hashes.add(digest.lower())
            returned = len(items)
            offset += returned
            if returned < page_size:
                return {"ids": ids, "hashes": hashes}
        # Refuse to report a complete index when the cap was reached.
        raise CloudStateError("control_api_seen_index_page_limit")

    def get_approved_queue(self, limit: int = 25) -> list[dict[str, Any]]:
        bounded = min(100, max(1, int(limit)))
        response = self._request("GET", "/api/brain/queue?limit=" + str(bounded))
        items = response.get("items")
        return items if isinstance(items, list) else []
