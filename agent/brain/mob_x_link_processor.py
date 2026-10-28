"""Run one explicitly confirmed Mob X link to durable pending-review state.

No channel publishing occurs in this module. It uses the existing Worker/D1,
translation ledger, public metadata mirrors and MEGA storage wrapper.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import os
import re
import socket
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from agent.brain import speech_gate
from agent.brain.cloud_state import CloudState
from agent.brain.inspector import inspect_media
from agent.brain.models import Candidate
from agent.brain.mob_translation import translate_to_persian
from scripts import subtitle_pipeline
from agent.brain.official import evaluate_official_media
from agent.brain.pipeline import StagedMedia
from agent.storage.mega import MegaStorage
from agent.brain.mob_x_rubika import (
    upload_preview_media, send_owner_preview, send_owner_translation_failure,
    send_owner_worker_request_failure,
)

MAX_MEDIA_BYTES = 100 * 1024 * 1024
MAX_VIDEO_SECONDS = 600
TIMEOUT = 20
WORKER_REQUEST_ATTEMPTS = 3
WORKER_RETRY_BACKOFF_SECONDS = (2, 5)
HANDLE = "mobgamesstudios"
LINK_RE = re.compile(r"^https://(?:(?:www|mobile)\.)?(?:x\.com|twitter\.com)/mobgamesstudios/status/([0-9]{15,22})/?$", re.I)
MEDIA_HOSTS = {"pbs.twimg.com", "video.twimg.com"}


def _safe_exception_message(exc: BaseException) -> str:
    """Return a short exception message safe for logs and the owner's private notification."""
    rendered = str(exc or "")
    for name, value in os.environ.items():
        upper_name = name.upper()
        if value and len(value) >= 4 and any(
            marker in upper_name for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "COOKIE", "CREDENTIAL", "OWNER_CHAT_ID")
        ):
            rendered = rendered.replace(value, "[REDACTED]")
    rendered = re.sub(r"https?://\S+", "[URL omitted]", rendered, flags=re.IGNORECASE)
    rendered = re.sub(
        r"(?i)\b(?:token|password|secret|authorization|cookie|api[_-]?key)\s*[:=]\s*\S+",
        "[REDACTED]",
        rendered,
    )
    rendered = re.sub(r"\b[A-Za-z0-9_-]{32,}\b", "[REDACTED]", rendered)
    rendered = re.sub(r"[\x00-\x1f\x7f]+", " ", rendered)
    return " ".join(rendered.split()).strip()[:150]


class MobXLinkError(RuntimeError):
    def __init__(
        self, code: str, *, error_class: str | None = None, error_detail: str | None = None,
    ):
        super().__init__(code)
        self.code = code if re.fullmatch(r"[a-z0-9_-]{1,80}", code) else "mob_x_processing_failed"
        self.error_class = (
            error_class[:80]
            if isinstance(error_class, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", error_class)
            else None
        )
        self.error_detail = str(error_detail or "")[:150] if error_detail is not None else None


def parse_official_link(value: str) -> tuple[str, str] | None:
    match = LINK_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    external_id = match.group(1)
    return f"https://x.com/{HANDLE}/status/{external_id}", external_id


def clean_caption(value: object, limit: int = 5000) -> str:
    text = str(value or "").replace(chr(0), "").strip()
    text = re.sub(r"https?://\S+", "", text)
    text = text.replace(chr(96), "")
    text = re.sub(r"[*_~]{1,3}", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:max(0, limit)]


def _safe_markdown_body(value: object, limit: int = 4000) -> str:
    """Keep caption content plain so source markup cannot inject Rubika metadata."""
    body = clean_caption(value, limit)
    body = body.replace("$", "＄")
    return re.sub(r"(?m)^>", "›", body)


def build_mob_x_caption(original_caption: object, translation: object = None) -> str:
    """Format a media-backed official X post for Rubika bold and quote metadata."""
    source = _safe_markdown_body(original_caption, 5000)
    if not source:
        return "**ماب انترتینمنت در پیج X خود پستی منتشر کرد.**"

    heading = "**ماب انترتینمنت در پیج خود پستی منتشر کرد و گفت:**"
    translated = _safe_markdown_body(translation, 4000)
    if not translated:
        return f"{heading}\n\n**{source}**"
    return f"{heading}\n\n**{source}**\n\n$**ترجمه متن:**\n**{translated}**$"


def build_owner_preview_caption(
    caption: str, media_type: str, subtitle_processing: dict[str, Any] | None,
) -> str:
    """Add video subtitle status to the private owner preview only."""
    if media_type != "video":
        return caption
    state = subtitle_processing if isinstance(subtitle_processing, dict) else {}
    if state.get("status") == "burned":
        try:
            cue_count = max(0, int(state.get("cues") or 0))
        except (TypeError, ValueError):
            cue_count = 0
        status_line = f"زیرنویس فارسی: دارد ({cue_count} خط)"
        if state.get("asr_fallback_reason") == "medium_runtime_exceeded_15m":
            status_line += "\nمدل زیرنویس به small برگشت؛ اجرای medium بیش از ۱۵ دقیقه طول کشید."
    elif state.get("status") == "skipped_no_detectable_speech":
        status_line = "بدون زیرنویس: گفتار تشخیص داده نشد (no_cues)"
    else:
        status_line = "بدون زیرنویس: ویدیو گفتار ندارد"
    base = str(caption or "").rstrip()
    return f"{base}\n\n{status_line}" if base else status_line


def run_video_speech_gate(path: str | Path, inspection: Any) -> dict:
    """Classify speech before a video can be staged; ASR failure never bypasses the gate."""
    probe = {
        "format": {"duration": float(inspection.duration_seconds or 0)},
        "streams": [{"codec_type": "video"}],
    }
    segments = []
    language = None
    language_probability = 0.0
    if inspection.has_audio:
        probe["streams"].append({"codec_type": "audio"})
    try:
        if inspection.has_audio:
            segments, language, language_probability = speech_gate.transcribe(
                str(path), model_size="small",
            )
        result = speech_gate.evaluate(probe, segments, language, language_probability)
    except Exception as exc:
        error_class = type(exc).__name__
        error_detail = _safe_exception_message(exc)
        print(json.dumps({
            "event": "mob_x_speech_gate_failed",
            "error_code": "mob_x_speech_gate_failed",
            "error_class": error_class,
            "error_detail": error_detail,
        }, ensure_ascii=False, sort_keys=True))
        raise MobXLinkError(
            "mob_x_speech_gate_failed", error_class=error_class, error_detail=error_detail,
        ) from None
    return {
        "decision": result.decision,
        "reason": result.reason,
        "speech_seconds": round(float(result.speech_seconds), 3),
        "speech_ratio": round(float(result.speech_ratio), 4),
        "language": result.language,
        "language_prob": round(float(result.language_prob), 4),
    }


def author_is_official(value: object) -> bool:
    if isinstance(value, dict):
        value = value.get("screen_name") or value.get("username") or value.get("user_screen_name") or value.get("handle")
    return str(value or "").strip().lstrip("@").casefold() == HANDLE


def _fxtwitter(payload: dict, external_id: str) -> dict:
    post = payload.get("tweet") or payload.get("status")
    if not isinstance(post, dict):
        raise MobXLinkError("mob_x_metadata_shape_invalid")
    if not author_is_official(post.get("author") or post.get("user") or post.get("screen_name")):
        raise MobXLinkError("mob_x_author_mismatch")
    actual_id = post.get("id") or post.get("tweetID") or post.get("rest_id")
    if str(actual_id or "") != external_id:
        raise MobXLinkError("mob_x_post_id_mismatch")
    media = post.get("media") if isinstance(post.get("media"), dict) else {}
    rows = []
    for key in ("all", "videos", "photos", "images"):
        rows.extend(row for row in media.get(key, []) if isinstance(row, dict))
    return {
        "provider": "fxtwitter", "external_id": external_id,
        "text": post.get("text") or post.get("full_text") or post.get("caption") or "",
        "title": post.get("title") or "",
        "published_at": post.get("created_at") or post.get("date") or post.get("published_at"),
        "media": rows, "media_url_list": [],
    }


def _vxtwitter(payload: dict, external_id: str) -> dict:
    author = payload.get("user_screen_name") or payload.get("screen_name") or payload.get("username") or payload.get("author")
    if not author_is_official(author):
        raise MobXLinkError("mob_x_author_mismatch")
    actual_id = payload.get("tweetID") or payload.get("id") or payload.get("rest_id")
    if str(actual_id or "") != external_id:
        raise MobXLinkError("mob_x_post_id_mismatch")
    media = payload.get("media_extended")
    rows = [row for row in media if isinstance(row, dict)] if isinstance(media, list) else []
    urls = payload.get("mediaURLs")
    urls = [str(item) for item in urls if isinstance(item, str)] if isinstance(urls, list) else []
    return {
        "provider": "vxtwitter", "external_id": external_id,
        "text": payload.get("text") or payload.get("tweet") or payload.get("description") or "",
        "title": payload.get("title") or "",
        "published_at": payload.get("date") or payload.get("created_at") or payload.get("date_epoch"),
        "media": rows, "media_url_list": urls,
    }


def _fetch_provider(session: requests.Session, provider: str, external_id: str, timeout: int = TIMEOUT):
    if provider == "fxtwitter":
        endpoint = f"https://api.fxtwitter.com/{HANDLE}/status/{external_id}"
        parser = _fxtwitter
    else:
        endpoint = f"https://api.vxtwitter.com/Twitter/status/{external_id}"
        parser = _vxtwitter
    summary = {
        "provider": provider, "http_status": None, "author_verified": False,
        "id_verified": False, "json_valid": False, "media_count": 0, "error_code": None,
    }
    try:
        response = session.get(
            endpoint, headers={"Accept": "application/json", "User-Agent": "RubikaAgent-MobXLink/1.0"},
            timeout=timeout, allow_redirects=False,
        )
        summary["http_status"] = response.status_code
        if response.status_code != 200:
            summary["error_code"] = f"mob_x_metadata_http_{response.status_code}"
            return None, summary
        try:
            payload = response.json()
        except (ValueError, TypeError):
            summary["error_code"] = "mob_x_metadata_invalid_json"
            return None, summary
        if not isinstance(payload, dict):
            summary["error_code"] = "mob_x_metadata_invalid_json"
            return None, summary
        summary["json_valid"] = True
        try:
            post = parser(payload, external_id)
        except MobXLinkError as exc:
            summary["error_code"] = exc.code
            return None, summary
        summary.update(author_verified=True, id_verified=True, media_count=len(post["media"]) + len(post["media_url_list"]))
        return post, summary
    except requests.RequestException:
        summary["error_code"] = "mob_x_metadata_request_failed"
        return None, summary


def probe_public_sources(external_id: str, *, session: requests.Session | None = None) -> dict:
    if not re.fullmatch(r"[0-9]{15,22}", external_id):
        raise MobXLinkError("invalid_external_id")
    session = session or requests.Session()
    results, usable = [], []
    for provider in ("fxtwitter", "vxtwitter"):
        post, summary = _fetch_provider(session, provider, external_id, timeout=10)
        results.append(summary)
        if post is not None:
            usable.append(provider)
    return {
        "mode": "read_only_public_api_probe", "external_id_tail": external_id[-6:],
        "api_keys_used": False, "media_downloaded": False, "publishing_attempted": False,
        "results": results, "usable_sources": usable, "available": bool(usable),
    }


def fetch_public_post(external_id: str, *, session: requests.Session | None = None) -> dict:
    if not re.fullmatch(r"[0-9]{15,22}", external_id):
        raise MobXLinkError("invalid_external_id")
    session = session or requests.Session()
    summaries = []
    for provider in ("fxtwitter", "vxtwitter"):
        post, summary = _fetch_provider(session, provider, external_id)
        summaries.append(summary)
        if post is not None:
            return post
    codes = {item.get("error_code") for item in summaries}
    for code in ("mob_x_author_mismatch", "mob_x_post_id_mismatch", "mob_x_metadata_http_403", "mob_x_metadata_http_404", "mob_x_metadata_http_429"):
        if code in codes:
            raise MobXLinkError(code)
    raise MobXLinkError("mob_x_source_unavailable")


def _safe_media_url(value: str, kind: str) -> str:
    url = urlparse(str(value or ""))
    host = (url.hostname or "").lower().rstrip(".")
    if (
        url.scheme != "https" or url.username or url.password or url.port not in (None, 443)
        or host not in MEDIA_HOSTS
        or (kind == "image" and host != "pbs.twimg.com")
        or (kind == "video" and host != "video.twimg.com")
    ):
        raise MobXLinkError("mob_x_media_host_rejected")
    try:
        addresses = {record[4][0] for record in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses:
            raise MobXLinkError("mob_x_media_dns_failed")
        for address in addresses:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
            if not ip.is_global:
                raise MobXLinkError("mob_x_media_host_rejected")
    except MobXLinkError:
        raise
    except OSError:
        raise MobXLinkError("mob_x_media_dns_failed") from None
    return url.geturl()


def select_media(post: dict) -> tuple[str, str]:
    for row in post.get("media") or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("type") or row.get("media_type") or "").casefold()
        candidate = row.get("url") or row.get("media_url_https") or row.get("media_url") or row.get("src")
        variants = row.get("variants")
        if not candidate and kind in {"video", "animated_gif", "gif"} and isinstance(variants, list):
            valid = [v for v in variants if isinstance(v, dict) and v.get("url") and str(v.get("content_type") or "").lower() in {"video/mp4", ""}]
            valid.sort(key=lambda v: int(v.get("bitrate") or 0), reverse=True)
            if valid:
                candidate = valid[0]["url"]
        if kind in {"video", "animated_gif", "gif"} and candidate:
            return "video", _safe_media_url(candidate, "video")
        if kind in {"photo", "image", "photos"} and candidate:
            return "image", _safe_media_url(candidate, "image")
    for candidate in post.get("media_url_list") or []:
        path = urlparse(str(candidate)).path.casefold()
        if re.search(r"\.(?:mp4|m4v|mov|webm)(?:$|/)", path):
            return "video", _safe_media_url(candidate, "video")
        if re.search(r"\.(?:jpe?g|png|webp|gif)(?:$|/)", path):
            return "image", _safe_media_url(candidate, "image")
    raise MobXLinkError("mob_x_media_missing")


def download_public_media(
    url: str, kind: str, folder: str | Path, *,
    session: requests.Session | None = None, max_bytes: int = MAX_MEDIA_BYTES,
) -> Path:
    if kind not in {"image", "video"}:
        raise MobXLinkError("mob_x_media_type_unsupported")
    current = _safe_media_url(url, kind)
    session = session or requests.Session()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    response = None
    try:
        for redirects in range(4):
            current = _safe_media_url(current, kind)
            response = session.get(
                current, headers={"Accept": "image/*, video/*;q=0.9, */*;q=0.1", "User-Agent": "RubikaAgent-MobXLink/1.0"},
                timeout=TIMEOUT, stream=True, allow_redirects=False,
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                response.close()
                response = None
                if redirects == 3 or not location:
                    raise MobXLinkError("mob_x_media_redirect_limit")
                current = urljoin(current, location)
                continue
            if response.status_code != 200:
                raise MobXLinkError(f"mob_x_media_http_{response.status_code}")
            if _safe_media_url(str(response.url or current), kind) != current:
                raise MobXLinkError("mob_x_media_redirect_rejected")
            mime = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
            if (kind == "image" and not mime.startswith("image/")) or (
                kind == "video" and not (mime.startswith("video/") or mime == "application/octet-stream")
            ):
                raise MobXLinkError("mob_x_expected_" + kind)
            length = response.headers.get("Content-Length")
            if length:
                try:
                    if int(length) > max_bytes:
                        raise MobXLinkError("mob_x_video_too_large" if kind == "video" else "mob_x_image_too_large")
                except ValueError:
                    raise MobXLinkError("mob_x_media_content_length_invalid") from None
            suffix = { "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
                       "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov" }.get(mime)
            suffix = suffix or mimetypes.guess_extension(mime) or (".mp4" if kind == "video" else ".jpg")
            if kind == "video" and suffix not in {".mp4", ".m4v", ".mov", ".webm", ".mkv"}:
                suffix = ".mp4"
            if kind == "image" and suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                suffix = ".jpg"
            destination = folder / ("mob_x_media" + suffix)
            size = 0
            try:
                with destination.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            destination.unlink(missing_ok=True)
                            raise MobXLinkError("mob_x_video_too_large" if kind == "video" else "mob_x_image_too_large")
                        output.write(chunk)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            if size <= 1024:
                destination.unlink(missing_ok=True)
                raise MobXLinkError("mob_x_media_too_small")
            return destination
        raise MobXLinkError("mob_x_media_redirect_limit")
    except MobXLinkError:
        raise
    except requests.RequestException:
        raise MobXLinkError("mob_x_media_download_failed") from None
    finally:
        if response is not None:
            response.close()


def normalize_published_at(value: object) -> str | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError):
        return None


class WorkerClient:
    def __init__(self, base_url: str, token: str, *, session: requests.Session | None = None):
        if not base_url.startswith("https://") or len(token.strip()) < 32:
            raise MobXLinkError("worker_credentials_missing_or_invalid")
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.session = session or requests.Session()

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        started = time.monotonic()
        safe_path = urlparse(str(path or "")).path or "/"
        safe_path = re.sub(r"[^A-Za-z0-9_./-]", "_", safe_path)
        safe_path = re.sub(r"\b[A-Za-z0-9_-]{32,}\b", "[redacted]", safe_path)
        if not safe_path.startswith("/"):
            safe_path = "/" + safe_path

        def failure_detail(attempts: int, *, exception_name: str | None = None,
                           status: int | None = None) -> str:
            parts = [f"path={safe_path}", f"attempts={attempts}"]
            if status is not None:
                parts.append(f"status={status}")
            elif exception_name:
                parts.append(f"exception={exception_name}")
            parts.append(f"elapsed_ms={max(0, int((time.monotonic() - started) * 1000))}")
            return " ".join(parts)[:150]

        for attempt in range(1, WORKER_REQUEST_ATTEMPTS + 1):
            try:
                response = self.session.request(
                    method, self.base_url + path,
                    headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"},
                    json=payload, timeout=TIMEOUT,
                )
            except requests.RequestException as exc:
                if attempt < WORKER_REQUEST_ATTEMPTS:
                    time.sleep(WORKER_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                name = type(exc).__name__
                raise MobXLinkError(
                    "worker_request_failed", error_class=name,
                    error_detail=failure_detail(attempt, exception_name=name),
                ) from None

            status = int(response.status_code)
            if status == 429 or status >= 500:
                if attempt < WORKER_REQUEST_ATTEMPTS:
                    time.sleep(WORKER_RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                raise MobXLinkError(
                    "worker_request_failed", error_class="HTTPStatusError",
                    error_detail=failure_detail(attempt, status=status),
                )

            if status >= 400:
                try:
                    value = response.json()
                except (ValueError, TypeError):
                    value = None
                code = value.get("error") if isinstance(value, dict) else None
                code = str(code or "worker_http_error")
                raise MobXLinkError(
                    code if re.fullmatch(r"[a-z0-9_-]{1,80}", code) else "worker_http_error"
                )

            try:
                value = response.json()
            except (ValueError, TypeError) as exc:
                name = type(exc).__name__
                raise MobXLinkError(
                    "worker_request_failed", error_class=name,
                    error_detail=failure_detail(attempt, exception_name=name),
                ) from None
            if not isinstance(value, dict) or value.get("ok") is not True:
                raise MobXLinkError("worker_response_invalid")
            return value

        raise MobXLinkError(
            "worker_request_failed",
            error_detail=failure_detail(WORKER_REQUEST_ATTEMPTS, exception_name="RequestError"),
        )

    def claim(self, panel_link_id: int | None = None) -> dict | None:
        payload = {} if panel_link_id is None else {"panel_link_id": int(panel_link_id)}
        result = self.request("POST", "/api/console/mob-x/claim", payload)
        job = result.get("job")
        if result.get("claimed") is not True:
            return None
        if not isinstance(job, dict):
            raise MobXLinkError("mob_x_link_claim_payload_invalid")
        return job

    def rules(self) -> dict:
        rules = self.request("GET", "/api/console/rules").get("rules")
        if not isinstance(rules, dict):
            raise MobXLinkError("console_rules_invalid")
        return rules

    def transition(
        self, job_id: str, status: str, *, result: dict | None = None,
        error_code: str | None = None, error_detail: str | None = None,
    ):
        payload = {"status": status}
        if result is not None:
            payload["result"] = result
        if error_code is not None:
            payload["error_code"] = error_code
        if error_detail is not None and status == "failed":
            payload["error_detail"] = str(error_detail)[:150]
        self.request("POST", f"/api/console/mob-x/jobs/{job_id}/transition", payload)

    def record_preview(self, job_id: str, message_id: str | None) -> None:
        payload = {"preview_sent": True}
        if message_id:
            payload["preview_message_id"] = str(message_id)
        self.request("POST", f"/api/console/mob-x/jobs/{job_id}/preview", payload)


def process_job(job: dict, client: WorkerClient, *, session: requests.Session | None = None,
                storage: MegaStorage | None = None) -> dict:
    job_id = str(job.get("id") or "")
    if not re.fullmatch(r"job_[A-Fa-f0-9]{8,80}", job_id):
        raise MobXLinkError("mob_x_job_id_invalid")
    params = job.get("params") if isinstance(job.get("params"), dict) else {}
    parsed = parse_official_link(str(params.get("url") or job.get("url") or ""))
    external_id = str(job.get("external_id") or "")
    if not parsed or parsed[1] != external_id:
        raise MobXLinkError("mob_x_link_identity_mismatch")
    canonical_url, _ = parsed

    cloud = CloudState(
        os.getenv("AGENT_API_BASE_URL", "https://rubika-agent.h98609294.workers.dev"),
        token=os.getenv("AGENT_API_TOKEN", ""), timeout_seconds=20,
    )
    seen = cloud.get_seen_index()
    if "mob_official:" + external_id in seen.get("ids", set()):
        raise MobXLinkError("mob_x_post_already_delivery_blocked")

    post = fetch_public_post(external_id, session=session)
    original = str(post.get("text") or "").strip()[:5000]
    # Resolve media first: text-only posts must never enter the caption/translation path.
    media_type, media_url = select_media(post)
    # Load worker-controlled caption rules once before ffprobe, Whisper or translation.
    rules = client.rules()
    with tempfile.TemporaryDirectory(prefix="rubika-agent-mob-x-") as temp:
        local_path = download_public_media(media_url, media_type, temp, session=session)
        inspection = inspect_media(local_path, media_type, max_bytes=MAX_MEDIA_BYTES)
        if not inspection.ok:
            if "file_size_limit" in inspection.errors:
                raise MobXLinkError("mob_x_video_too_large" if media_type == "video" else "mob_x_image_too_large")
            raise MobXLinkError("mob_x_media_validation_failed")
        if media_type == "video" and inspection.duration_seconds and inspection.duration_seconds > MAX_VIDEO_SECONDS:
            raise MobXLinkError("mob_x_video_duration_limit")

        # All videos pass the speech gate before any staging or owner preview.
        speech_gate_result = run_video_speech_gate(local_path, inspection) if media_type == "video" else None
        publish_path = str(local_path)
        subtitle_processing = {"status": "not_applicable"} if media_type != "video" else {
            "status": "skipped_by_speech_gate",
            "reason": str((speech_gate_result or {}).get("reason") or "speech_not_detected"),
        }

        if media_type == "video" and speech_gate_result and speech_gate_result.get("decision") == "subtitle":
            language = str(speech_gate_result.get("language") or "")
            if not re.fullmatch(r"[a-z]{2,3}", language):
                language = "en"

            def transcribe_subtitles(video_path: str) -> list:
                # faster-whisper is pinned and installed only by the Mob X workflow.
                return subtitle_pipeline.transcribe(video_path, model_size="medium", language=language)

            def translate_subtitle_batch(prompt: str, *, max_tokens: int) -> str:
                if max_tokens != subtitle_pipeline.MAX_OUT_TOKENS:
                    raise subtitle_pipeline.SubtitleError("token_limit_invalid")
                numbered_lines = str(prompt).rsplit("\n\n", 1)[-1].strip()
                translated = translate_to_persian(numbered_lines, kind="subtitle")
                return translated or ""

            try:
                subtitle_result = subtitle_pipeline.build_subtitled_video(
                    str(local_path), str(Path(temp) / "subtitle-work"), translate_subtitle_batch,
                    fontsdir="assets/fonts", font="Estedad",
                    transcribe_fn=transcribe_subtitles, max_missing_ratio=0.0,
                )
            except subtitle_pipeline.SubtitleError as exc:
                if exc.code == "no_cues":
                    # Repeated ASR found no usable speech cues; retain the original video.
                    subtitle_processing = {"status": "skipped_no_detectable_speech"}
                elif exc.code == "translation_incomplete":
                    # Never burn or stage partially translated subtitles.
                    raise MobXLinkError("mob_x_translation_failed") from None
                elif exc.code == "audio_decode_failed":
                    # Fail closed and preserve sanitized decoder details for the owner PV alert.
                    raise MobXLinkError(
                        "mob_x_subtitle_audio_decode_failed",
                        error_class=type(exc).__name__,
                        error_detail=_safe_exception_message(exc.detail or exc),
                    ) from None
                else:
                    raise MobXLinkError("mob_x_subtitle_" + exc.code) from None
            except Exception:
                raise MobXLinkError("mob_x_subtitle_processing_failed") from None
            else:
                processed_path = str(subtitle_result.get("out") or "")
                if not processed_path:
                    raise MobXLinkError("mob_x_subtitle_output_validation_failed")
                processed_inspection = inspect_media(
                    processed_path, "video", max_bytes=MAX_MEDIA_BYTES,
                )
                if not processed_inspection.ok or not processed_inspection.has_audio:
                    raise MobXLinkError("mob_x_subtitle_output_validation_failed")
                source_duration = float(inspection.duration_seconds or 0)
                rendered_duration = float(processed_inspection.duration_seconds or 0)
                if (
                    source_duration <= 0 or rendered_duration <= 0
                    or abs(source_duration - rendered_duration) >
                    subtitle_pipeline.DURATION_TOLERANCE_MS / 1000
                ):
                    raise MobXLinkError("mob_x_subtitle_output_validation_failed")
                publish_path = processed_path
                inspection = processed_inspection
                subtitle_processing = {
                    "status": "burned",
                    "cues": int(subtitle_result.get("cues") or 0),
                    "batches": int(subtitle_result.get("batches") or 0),
                    "missing": int(subtitle_result.get("missing") or 0),
                    "size_bytes": int(subtitle_result.get("size") or 0),
                    "duration_ms": int(subtitle_result.get("duration_ms") or 0),
                    "asr_initial_model": str(subtitle_result.get("asr_initial_model") or "medium"),
                    "asr_model": str(subtitle_result.get("asr_model") or "medium"),
                    "asr_fallback_reason": subtitle_result.get("asr_fallback_reason"),
                    "asr_elapsed_seconds": float(subtitle_result.get("asr_elapsed_seconds") or 0),
                    "asr_medium_elapsed_seconds": float(subtitle_result.get("asr_medium_elapsed_seconds") or 0),
                    "asr_fallback_elapsed_seconds": float(subtitle_result.get("asr_fallback_elapsed_seconds") or 0),
                }

        translation_source = clean_caption(original, 5000)
        translation = None
        if translation_source:
            try:
                caption_limit = max(1, min(4000, int(rules.get("caption_max_chars", 900))))
            except (TypeError, ValueError):
                caption_limit = 900
            if rules.get("translate_enabled", True) is True:
                try:
                    translation = clean_caption(translate_to_persian(translation_source), caption_limit)
                except Exception:
                    translation = ""
                if not translation:
                    raise MobXLinkError("mob_x_translation_failed")
        caption = build_mob_x_caption(translation_source, translation)
        candidate = Candidate(
            platform="mob_official", canonical_url=canonical_url, external_id=external_id,
            title=clean_caption(post.get("title"), 500) or original[:500],
            caption=caption, source_key=HANDLE, media_type=media_type,
            source_tier="official", published_at=normalize_published_at(post.get("published_at")),
            original_caption=original, persian_translation=translation,
        )
        evaluation = evaluate_official_media(candidate, inspection)
        if evaluation.decision != "eligible":
            raise MobXLinkError("mob_x_media_validation_failed")
        selected_storage = storage or MegaStorage()
        storage_key = selected_storage.upload(publish_path, "official")
        rubika_token = os.getenv("RUBIKA_TOKEN", "")
        owner_chat_id = os.getenv("RUBIKA_OWNER_CHAT_ID", "")
        if not rubika_token.strip() or not owner_chat_id.strip():
            raise MobXLinkError("mob_x_preview_credentials_missing")
        try:
            rubika_file_id = upload_preview_media(rubika_token, publish_path, media_type)
        except Exception:
            raise MobXLinkError("mob_x_preview_upload_failed") from None
        staged = StagedMedia(
            candidate=candidate, original_path=str(local_path), publish_path=str(publish_path),
            inspection=inspection, evaluation=evaluation, storage_key=storage_key, caption=caption,
        )
        payload = CloudState.prepare_staged_payload(staged)
        payload.update({
            "caption": caption, "original_caption": original, "persian_translation": translation,
            "platform": "mob_official", "source_tier": "official",
            "review_state": "pending", "status": "ready",
        })
        cloud_result = cloud.upsert_item(payload)
        result = {
            "item_id": cloud_result.get("item_id") or "mob_official:" + external_id,
            "external_id": external_id, "url": canonical_url,
            "title": candidate.title, "published_at": candidate.published_at,
            "media_type": media_type, "caption": caption,
            "original_caption": original, "persian_translation": translation,
            "media_url": media_url, "storage_key": storage_key,
            "source_tier": "official", "review_state": "pending",
            "rubika_file_id": rubika_file_id, "preview_sent": False,
            "publishing_attempted": False, "speech_gate": speech_gate_result,
            "subtitle_processing": subtitle_processing,
        }
        client.transition(job_id, "ready_for_review", result=result)
        try:
            preview_caption = build_owner_preview_caption(
                caption, media_type, subtitle_processing,
            )
            preview_message_id = send_owner_preview(
                rubika_token, owner_chat_id, rubika_file_id, preview_caption, job_id,
            )
        except Exception:
            raise MobXLinkError("mob_x_owner_preview_failed") from None
        try:
            client.record_preview(job_id, preview_message_id)
        except MobXLinkError:
            # The preview and controls were sent; preserve the durable reusable file_id.
            pass
        return {
            "status": "ready_for_review", "job_id": job_id,
            "item_id": result["item_id"], "external_id": external_id,
            "speech_gate": speech_gate_result, "subtitle_processing": subtitle_processing,
        }


def _write_report(report: dict, path: str | None, exit_code: int) -> int:
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
    print(rendered)
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write("## Mob X link processor\n\n")
            stream.write(f"Status: {report.get('status', 'unknown')}\n")
            if report.get("job_id"):
                stream.write(f"Job: {report['job_id']}\n")
            if report.get("error_code"):
                stream.write(f"Error code: {report['error_code']}\n")
            stream.write("Publishing attempted: no\n")
    return exit_code


def process_one(report_path: str | None = None, panel_link_id: int | None = None) -> int:
    report = {"mode": "process_one", "publishing_attempted": False}
    client = None
    job = None
    failure_detail = None
    failure_class = None
    try:
        client = WorkerClient(
            os.getenv("AGENT_API_BASE_URL", "https://rubika-agent.h98609294.workers.dev"),
            os.getenv("AGENT_API_TOKEN", ""),
        )
        job = client.claim(panel_link_id)
        if job is None:
            report.update(status="idle", reason="no_queued_mob_x_link")
            return _write_report(report, report_path, 0)
        report.update(status="processing", job_id=job.get("id"))
        report.update(process_job(job, client))
        return _write_report(report, report_path, 0)
    except MobXLinkError as exc:
        code = exc.code
        failure_detail = exc.error_detail
        failure_class = exc.error_class
        if failure_class:
            report["error_class"] = failure_class
        if failure_detail:
            report["error_detail"] = failure_detail
    except Exception:
        code = "mob_x_processor_internal_error"
    report.update(status="failed", error_code=code)
    worker_failure_detail = failure_detail if code == "worker_request_failed" else None
    if client is not None and job and isinstance(job.get("id"), str):
        try:
            client.transition(
                job["id"], "failed", error_code=code,
                error_detail=failure_detail if code == "worker_request_failed" else None,
            )
        except MobXLinkError as transition_exc:
            report["transition_error"] = transition_exc.code
            if transition_exc.error_detail:
                report["transition_error_detail"] = transition_exc.error_detail
            if transition_exc.code == "worker_request_failed":
                worker_failure_detail = transition_exc.error_detail or worker_failure_detail
        except Exception:
            report["transition_error"] = "mob_x_failure_transition_failed"
        if code == "mob_x_translation_failed":
            try:
                send_owner_translation_failure(
                    os.getenv("RUBIKA_TOKEN", ""), os.getenv("RUBIKA_OWNER_CHAT_ID", ""), job["id"],
                )
                report["owner_pv_notice"] = "sent"
            except Exception:
                report["owner_pv_notice"] = "failed"
    if worker_failure_detail:
        try:
            send_owner_worker_request_failure(
                os.getenv("RUBIKA_TOKEN", ""), os.getenv("RUBIKA_OWNER_CHAT_ID", ""),
                str(job.get("id") or "") if isinstance(job, dict) else "",
                worker_failure_detail,
            )
            report["owner_pv_notice"] = "sent"
        except Exception:
            report["owner_pv_notice"] = "failed"
        report["error_detail"] = worker_failure_detail
    return _write_report(report, report_path, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--process-one", action="store_true")
    parser.add_argument("--external-id", default="2107878755079213476")
    parser.add_argument("--panel-link-id", default="")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)
    if args.panel_link_id and not re.fullmatch(r"[1-9][0-9]{0,8}", args.panel_link_id):
        parser.error("--panel-link-id must be a positive numeric link ID")
    if args.probe == args.process_one:
        parser.error("choose exactly one of --probe or --process-one")
    try:
        if args.probe:
            result = probe_public_sources(args.external_id)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            summary = os.getenv("GITHUB_STEP_SUMMARY")
            if summary:
                with Path(summary).open("a", encoding="utf-8") as stream:
                    stream.write("## Free X metadata API probe (read-only)\n\n")
                    stream.write("No API key, media download or publication is used.\n\n")
                    stream.write("| Source | HTTP | Author verified | ID verified | Media count | Error code |\n|---|---:|---|---|---:|---|\n")
                    for row in result["results"]:
                        stream.write(
                            f"| {row['provider']} | {row['http_status'] or '-'} | {row['author_verified']} | "
                            f"{row['id_verified']} | {row['media_count']} | {row['error_code'] or '-'} |\n"
                        )
            return 0
        return process_one(
            args.report or None,
            panel_link_id=int(args.panel_link_id) if args.panel_link_id else None,
        )
    except MobXLinkError as exc:
        print(json.dumps({"status": "failed", "error_code": exc.code}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
