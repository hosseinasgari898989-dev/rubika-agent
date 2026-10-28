#!/usr/bin/env python3
"""Read-only Worker preflight for a confirmed Mob X link.

Failures are reported with bounded, sanitized diagnostics; API tokens and exception
messages are never included in output.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable


BODY_TIMEOUT_SECONDS = 20
BACKOFF_SECONDS = (2, 5)
MAX_ATTEMPTS = 3


def _safe_detail(endpoint: str, attempt: int, status: int | None, error_class: str) -> str:
    raw = (
        f"endpoint={endpoint} attempts={attempt} "
        f"status={status if status is not None else 'none'} error={error_class}"
    )
    return re.sub(r"[^A-Za-z0-9 _.=-]", "_", raw)[:200]


def _append_output(key: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    escaped = str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"{key}={escaped}\n")


def fetch_items(
    endpoint: str,
    base_url: str,
    path: str,
    token: str,
    *,
    opener: Callable | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> tuple[list[dict], str | None, int]:
    """Fetch an {ok: true, items: [...]} response, retrying transient failures."""
    open_url = opener or urllib.request.urlopen
    sleep = sleeper or time.sleep
    last_detail: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        response_status: int | None = None
        try:
            request = urllib.request.Request(
                base_url.rstrip("/") + path,
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/json",
                    "User-Agent": "rubika-agent-worker",
                },
            )
            # urllib's socket timeout also applies while response.read() consumes the body.
            with open_url(request, timeout=BODY_TIMEOUT_SECONDS) as response:
                response_status = getattr(response, "status", None)
                body = response.read()
            payload = json.loads(body)
            if not (
                isinstance(payload, dict)
                and payload.get("ok") is True
                and isinstance(payload.get("items"), list)
            ):
                return [], _safe_detail(endpoint, attempt, response_status, "WorkerResponseValidationError"), attempt
            return [item for item in payload["items"] if isinstance(item, dict)], None, attempt
        except urllib.error.HTTPError as exc:
            response_status = int(exc.code)
            error_class = type(exc).__name__
            retryable = response_status == 429 or response_status >= 500
        except Exception as exc:
            # Never surface exception text; it may contain URLs or other private context.
            error_class = type(exc).__name__
            retryable = True

        last_detail = _safe_detail(endpoint, attempt, response_status, error_class)
        if not retryable or attempt >= MAX_ATTEMPTS:
            return [], last_detail, attempt
        sleep(BACKOFF_SECONDS[attempt - 1])

    return [], last_detail or _safe_detail(endpoint, MAX_ATTEMPTS, None, "UnknownError"), MAX_ATTEMPTS


def main() -> int:
    base = os.environ.get("AGENT_API_BASE_URL", "").rstrip("/")
    token = os.environ.get("AGENT_API_TOKEN", "").strip()
    requested_link_id = os.environ.get("MOB_X_PANEL_LINK_ID", "").strip()
    if not base.startswith("https://") or not token:
        detail = "endpoint=configuration attempts=1 status=none error=CredentialsMissing"
        _append_output("error_detail", detail)
        print(json.dumps({"error_detail": detail}, sort_keys=True))
        raise SystemExit("mob_x_preflight_credentials_missing")
    if requested_link_id and not re.fullmatch(r"[1-9][0-9]{0,8}", requested_link_id):
        detail = "endpoint=configuration attempts=1 status=none error=PanelLinkIdInvalid"
        _append_output("error_detail", detail)
        print(json.dumps({"error_detail": detail}, sort_keys=True))
        raise SystemExit("mob_x_panel_link_id_invalid")

    jobs_path = "/api/console/jobs?" + urllib.parse.urlencode({
        "type": "mob_x_link", "status": "confirmed", "limit": 5,
    })
    jobs, jobs_error, jobs_attempts = fetch_items("jobs", base, jobs_path, token)
    links, links_error, links_attempts = fetch_items("links", base, "/api/console/links?limit=100", token)

    for endpoint, attempts, error in (
        ("jobs", jobs_attempts, jobs_error),
        ("links", links_attempts, links_error),
    ):
        if attempts > 1 and error is None:
            print(json.dumps({"endpoint": endpoint, "recovered_after_attempt": attempts}, sort_keys=True))

    if jobs_error or links_error:
        detail = jobs_error or links_error or "endpoint=unknown attempts=1 status=none error=WorkerApiUnavailable"
        _append_output("error_detail", detail)
        _append_output("has_job", "false")
        print(json.dumps({"error_detail": detail}, sort_keys=True))
        raise SystemExit("mob_x_preflight_worker_api_unavailable")

    links_by_id = {str(link.get("id")): link for link in links}
    has_job = any(
        job.get("type") == "mob_x_link"
        and job.get("status") == "confirmed"
        and str(job.get("panel_link_id")) in links_by_id
        and (not requested_link_id or str(job.get("panel_link_id")) == requested_link_id)
        and links_by_id[str(job.get("panel_link_id"))].get("status") in {"confirmed", "queued"}
        for job in jobs
    )
    _append_output("has_job", "true" if has_job else "false")
    print("has_job=true — confirmed Mob X work found" if has_job else
          "has_job=false — no confirmed Mob X work; downstream setup will be skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
