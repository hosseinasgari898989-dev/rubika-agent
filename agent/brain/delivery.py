"""Safe delivery state machine with bounded retries and post-success cleanup."""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any


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


def deliver_with_retry(
    *,
    item_id: str,
    send: Callable[[], Any],
    record_attempt: Callable[[int, str, str | None], None],
    delete_local: Callable[[], None],
    delete_remote: Callable[[], None] | None = None,
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
            data = response.get("data") if isinstance(response.get("data"), dict) else {}
            message_id = data.get("message_id") or response.get("message_id")
            record_attempt(attempt, "sent", None)
            cleanup_errors: list[str] = []
            local_removed = False
            try:
                delete_local()
                local_removed = True
            except Exception as exc:
                cleanup_errors.append("local_cleanup_" + type(exc).__name__)
            # Keep cloud copy as a safety net if local cleanup could not be verified.
            if local_removed and delete_remote is not None:
                try:
                    delete_remote()
                except Exception as exc:
                    cleanup_errors.append("remote_cleanup_" + type(exc).__name__)
            return {
                "item_id": item_id, "status": "sent", "attempts": attempt,
                "message_id": str(message_id) if message_id is not None else None,
                "cleanup": "complete" if not cleanup_errors else "pending",
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
        record_attempt(attempt, "uncertain", "ambiguous_response")
        return {
            "item_id": item_id, "status": "uncertain",
            "attempts": attempt, "error": "ambiguous_response", "cleanup": "retained",
        }

    return {"item_id": item_id, "status": "failed", "attempts": max_attempts}
