"""Safe delivery state machine with bounded retries and post-success cleanup."""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from typing import Any


_LOGGER = logging.getLogger(__name__)
_SAFE_KEY_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,40}")


def _response_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            payload = json.loads(value)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _message_id(response: Mapping[str, Any]) -> str | None:
    """Read a receipt from the documented/legacy Rubika response envelopes."""
    paths = (
        ("message_id",),
        ("messageId",),
        ("receipt_id",),
        ("data", "message_id"),
        ("data", "messageId"),
        ("data", "message", "message_id"),
        ("data", "message", "id"),
        ("data", "new_message", "message_id"),
        ("data", "new_message", "id"),
        ("data", "update", "new_message", "message_id"),
        ("data", "result", "message_id"),
        ("data", "receipt", "message_id"),
        ("data", "receipt", "id"),
        ("result", "message_id"),
        ("result", "message", "message_id"),
        ("result", "message", "id"),
        ("result", "data", "message_id"),
        ("receipt", "message_id"),
        ("receipt", "id"),
        ("raw_response", "message_id"),
        ("raw_response", "data", "message_id"),
        ("raw_response", "data", "message", "message_id"),
        ("raw_response", "data", "new_message", "message_id"),
    )
    for path in paths:
        current: Any = response
        for key in path:
            if not isinstance(current, Mapping):
                current = None
                break
            current = current.get(key)
        if type(current) in (str, int):
            value = str(current).strip()
            if value:
                return value
    return None


def _key_names(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    return sorted(key for key in value if isinstance(key, str) and _SAFE_KEY_NAME.fullmatch(key))


def _log_missing_receipt(response: Mapping[str, Any]) -> None:
    """Log response shape only; never log values, tokens, or destination identifiers."""
    data = response.get("data")
    data_message = data.get("message") if isinstance(data, Mapping) else None
    data_new_message = data.get("new_message") if isinstance(data, Mapping) else None
    data_receipt = data.get("receipt") if isinstance(data, Mapping) else None
    result = response.get("result")
    raw_response = response.get("raw_response")
    key_sets = {
        "response": _key_names(response),
        "data": _key_names(data),
        "data_message": _key_names(data_message),
        "data_new_message": _key_names(data_new_message),
        "data_receipt": _key_names(data_receipt),
        "result": _key_names(result),
        "raw_response": _key_names(raw_response),
    }
    _LOGGER.warning("Rubika delivery receipt missing; present_key_names=%s", key_sets)


def deliver_with_retry(
    *,
    item_id: str,
    send: Callable[[], Any],
    record_attempt: Callable[[int, str, str | None], None],
    delete_local: Callable[[], None],
    max_attempts: int = 3,
    sleep_seconds: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Retry definite API rejections at most 3 times; never retry ambiguous outcomes."""
    if max_attempts < 1 or max_attempts > 3:
        raise ValueError("max_attempts_must_be_between_1_and_3")

    for attempt in range(1, max_attempts + 1):
        record_attempt(attempt, "started", None)
        try:
            response = _response_dict(send())
        except Exception as exc:
            code = "transport_" + type(exc).__name__
            record_attempt(attempt, "uncertain", code)
            return {
                "item_id": item_id, "status": "uncertain",
                "attempts": attempt, "error": code, "cleanup": "retained",
            }

        status = response.get("status")
        if status == "OK":
            message_id = _message_id(response)
            if message_id is None:
                _log_missing_receipt(response)
                record_attempt(attempt, "uncertain", "delivery_receipt_missing")
                return {
                    "item_id": item_id, "status": "uncertain",
                    "attempts": attempt, "error": "delivery_receipt_missing",
                    "cleanup": "retained",
                }

            record_attempt(attempt, "sent", None)
            cleanup_errors: list[str] = []
            local_removed = False
            try:
                delete_local()
                local_removed = True
            except Exception as exc:
                cleanup_errors.append("local_cleanup_" + type(exc).__name__)
            return {
                "item_id": item_id, "status": "sent", "attempts": attempt,
                "message_id": message_id,
                "cleanup": "local_complete_remote_retained" if local_removed else "pending",
                "cleanup_errors": cleanup_errors,
            }

        if isinstance(status, str) and status and status != "OK":
            code = "rubika_rejected_" + "".join(
                ch for ch in status.lower() if ch.isalnum() or ch in "_-"
            )[:40]
            record_attempt(attempt, "failed", code)
            if attempt < max_attempts:
                sleeper(sleep_seconds)
                continue
            return {
                "item_id": item_id, "status": "failed", "attempts": attempt,
                "error": code, "cleanup": "retained",
            }

        # Empty/malformed response is ambiguous; retrying might duplicate a post.
        _log_missing_receipt(response)
        record_attempt(attempt, "uncertain", "ambiguous_response")
        return {
            "item_id": item_id, "status": "uncertain",
            "attempts": attempt, "error": "ambiguous_response", "cleanup": "retained",
        }

    return {"item_id": item_id, "status": "failed", "attempts": max_attempts}
