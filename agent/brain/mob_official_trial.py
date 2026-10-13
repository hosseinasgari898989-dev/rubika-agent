"""One-item official Mob post trial: discover, translate, validate, dedupe, then optionally publish.

Publishing is opt-in with --publish. The default mode is a dry-run and never sends
anything to Rubika. Ambiguous delivery is never retried automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import socket
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from agent.brain.cloud_state import CloudState, CloudStateError
from agent.brain.inspector import inspect_media
from agent.brain.mob_translation import format_mob_post
from agent.brain.rubika_publisher import RubikaPublisher
from agent.discovery.mob_official_complete import discover_complete_official_posts

MAX_MEDIA_BYTES = 100 * 1024 * 1024
MAX_VIDEO_SECONDS = 600
HTTP_TIMEOUT = (15, 45)
USER_AGENT = "RubikaAgent-MobOfficialTrial/1.0"


def _safe_public_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            return False
        if parsed.port not in {None, 443}:
            return False
        try:
            address = __import__("ipaddress").ip_address(host)
            return address.is_global
        except ValueError:
            # Source pages/CDNs are domain names. Do not follow explicit local names.
            if host in {"localhost", "metadata.google.internal"} or host.endswith(".local"):
                return False
            return True
    except ValueError:
        return False


def _youtube_or_vimeo(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return (
        host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")
        or host == "vimeo.com" or host.endswith(".vimeo.com")
    )


def _download_direct(url: str, kind: str, output_dir: Path) -> Path:
    if not _safe_public_url(url):
        raise RuntimeError("media_url_rejected")
    session = requests.Session()
    partial = output_dir / "official_media.part"
    try:
        with session.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "image/*, video/*;q=0.9, */*;q=0.1"},
            stream=True,
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
        ) as response:
            if response.status_code != 200:
                raise RuntimeError("media_http_" + str(response.status_code))
            final_url = str(getattr(response, "url", url) or url)
            if not _safe_public_url(final_url):
                raise RuntimeError("media_redirect_rejected")
            content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type in {"text/html", "application/json", "text/plain"}:
                raise RuntimeError("source_returned_non_media")
            if kind == "image" and not content_type.startswith("image/"):
                raise RuntimeError("expected_image_media")
            if kind == "video" and not content_type.startswith("video/") and content_type != "application/octet-stream":
                raise RuntimeError("expected_video_media")
            declared = response.headers.get("Content-Length")
            if declared:
                try:
                    if int(declared) > MAX_MEDIA_BYTES:
                        raise RuntimeError("media_size_limit")
                except ValueError:
                    raise RuntimeError("media_invalid_content_length") from None
            count = 0
            with partial.open("wb") as output:
                for chunk in response.iter_content(256 * 1024):
                    if not chunk:
                        continue
                    count += len(chunk)
                    if count > MAX_MEDIA_BYTES:
                        raise RuntimeError("media_size_limit")
                    output.write(chunk)
            if count <= 1024:
                raise RuntimeError("media_too_small")
            guessed = mimetypes.guess_extension(content_type) or ""
            suffix = Path(urlparse(final_url).path).suffix.lower()
            allowed = {".jpg", ".jpeg", ".png", ".webp"} if kind == "image" else {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
            if suffix not in allowed:
                suffix = guessed if guessed in allowed else (".jpg" if kind == "image" and content_type in {"image/jpeg", "image/jpg"} else ".png" if kind == "image" and content_type == "image/png" else ".webp" if kind == "image" and content_type == "image/webp" else ".mp4" if kind == "video" and content_type == "video/mp4" else "")
            if suffix not in allowed:
                raise RuntimeError("media_format_unsupported")
            destination = output_dir / ("official_media" + suffix)
            partial.replace(destination)
            return destination
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        session.close()


def _download_video_page(url: str, output_dir: Path) -> Path:
    if not _safe_public_url(url) or not _youtube_or_vimeo(url):
        raise RuntimeError("video_page_host_not_allowed")
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt_dlp_not_installed") from None

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "socket_timeout": 20,
        "retries": 1,
        "extractor_retries": 1,
        "max_filesize": MAX_MEDIA_BYTES,
        "match_filter": yt_dlp.utils.match_filter_func("duration <= 600"),
        "format": "best[ext=mp4][height<=1080]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(output_dir / "official_video.%(ext)s"),
        "overwrites": False,
        "cachedir": False,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except Exception as exc:
        # Keep the report actionable without exposing URLs, query parameters or provider bodies.
        raise RuntimeError("official_video_download_" + type(exc).__name__) from None
    candidates = [
        path for path in output_dir.glob("official_video.*")
        if path.is_file() and path.suffix.lower() in {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
    ]
    candidates = [path for path in candidates if path.stat().st_size > 0]
    if not candidates:
        raise RuntimeError("official_video_file_missing")
    chosen = max(candidates, key=lambda path: path.stat().st_size)
    if chosen.stat().st_size > MAX_MEDIA_BYTES:
        raise RuntimeError("media_size_limit")
    return chosen


def download_and_validate(item: dict[str, Any], output_dir: Path) -> tuple[Path, dict[str, Any]]:
    kind = str(item.get("media_type") or "")
    media_url = str(item.get("media_url") or "")
    if kind not in {"image", "video"} or not media_url:
        raise RuntimeError("original_media_unavailable")
    if kind == "video" and _youtube_or_vimeo(media_url):
        path = _download_video_page(media_url, output_dir)
    else:
        path = _download_direct(media_url, kind, output_dir)

    inspection = inspect_media(path, kind, max_bytes=MAX_MEDIA_BYTES)
    if not inspection.ok:
        path.unlink(missing_ok=True)
        raise RuntimeError("media_validation_failed_" + (inspection.errors[0] if inspection.errors else "unknown"))
    if kind == "video" and inspection.duration_seconds and inspection.duration_seconds > MAX_VIDEO_SECONDS:
        path.unlink(missing_ok=True)
        raise RuntimeError("video_duration_limit")
    if kind != inspection.media_type:
        path.unlink(missing_ok=True)
        raise RuntimeError("media_type_mismatch")
    return path, {
        "ok": inspection.ok,
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


def _parse_send_response(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _report(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_trial(*, publish: bool, limit: int, output_report: Path, output_dir: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "publish_one" if publish else "dry_run",
        "status": "failed",
        "publishing": False,
        "candidate_count": 0,
        "selected": None,
        "attempts": [],
        "error": None,
    }
    api_base = os.getenv("AGENT_API_BASE_URL", "https://rubika-agent.h98609294.workers.dev")
    token = os.getenv("AGENT_API_TOKEN", "")
    try:
        cloud = CloudState(api_base, token=token, timeout_seconds=20)
        seen = cloud.get_seen_index()
    except Exception as exc:
        report["error"] = "cloud_state_unavailable_" + type(exc).__name__
        _report(output_report, report)
        return report

    candidates, source_attempts = discover_complete_official_posts()
    report["attempts"] = source_attempts
    report["candidate_count"] = len(candidates)
    if not candidates:
        report["error"] = "no_post_with_original_caption_and_original_media"
        _report(output_report, report)
        return report

    selected = None
    for item in candidates:
        external_id = str(item.get("external_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", external_id):
            continue
        identity = "mob_official:" + external_id
        if identity in seen["ids"] or external_id in seen["ids"]:
            continue
        selected = item
        break
    if selected is None:
        report["status"] = "nothing_new"
        report["error"] = "all_complete_official_posts_already_known"
        _report(output_report, report)
        return report

    report["selected"] = {
        "source": selected.get("source_name"),
        "source_platform": selected.get("source_platform"),
        "external_id": selected.get("external_id"),
        "title": selected.get("title"),
        "published_at": selected.get("published_at"),
        "media_type": selected.get("media_type"),
    }

    formatted = format_mob_post(
        {
            "source_name": "ماب انترتینمنت",
            "original_caption": selected.get("original_caption"),
            "canonical_url": selected.get("canonical_url"),
            "title": selected.get("title"),
        }
    )
    if formatted["translation_status"] != "translated" or not formatted.get("original_caption") or not formatted.get("persian_translation"):
        report["error"] = "original_caption_or_persian_translation_unavailable"
        _report(output_report, report)
        return report
    caption = str(formatted["caption"])
    if re.search(r"https?://|www\.", caption, flags=re.IGNORECASE):
        report["error"] = "caption_contains_url"
        _report(output_report, report)
        return report

    output_dir.mkdir(parents=True, exist_ok=True)
    media_path: Path | None = None
    try:
        media_path, validation = download_and_validate(selected, output_dir)
        report["media"] = {
            "file_name": media_path.name,
            "size_bytes": media_path.stat().st_size,
            "sha256": validation["sha256"],
            "validation": validation,
        }
        if validation["sha256"] in seen["hashes"]:
            media_path.unlink(missing_ok=True)
            report["status"] = "duplicate_media"
            report["error"] = "media_hash_already_known"
            _report(output_report, report)
            return report

        payload = {
            "platform": "mob_official",
            "source_key": str(selected.get("source_key") or "mobentertainment.com"),
            "external_id": str(selected["external_id"]),
            "canonical_url": str(selected["canonical_url"]),
            "title": str(selected.get("title") or ""),
            "caption": caption,
            "original_caption": str(formatted["original_caption"]),
            "persian_translation": str(formatted["persian_translation"]),
            "published_at": selected.get("published_at"),
            "status": "ready",
            "review_state": "pending",
            "media_type": validation["media_type"],
            "source_tier": "official",
            "media_sha256": validation["sha256"],
            "media_bytes": validation["size_bytes"],
            "media_mime": validation["mime_type"],
            "validation": validation,
            "moderation": None,
            "storage_key": None,
        }
        report["caption"] = caption
        if not publish:
            report["status"] = "validated_preview"
            report["publishing"] = False
            _report(output_report, report)
            return report

        try:
            cloud.upsert_item(payload)
        except Exception as exc:
            report["error"] = "cloud_item_record_failed_" + type(exc).__name__
            _report(output_report, report)
            return report

        rubika_token = os.getenv("RUBIKA_TOKEN", "")
        channel_guid = os.getenv("RUBIKA_CHANNEL_GUID", "")
        if not rubika_token or not channel_guid:
            report["error"] = "rubika_credentials_missing"
            _report(output_report, report)
            return report

        publisher = RubikaPublisher(rubika_token, channel_guid)
        item_id = "mob_official:" + str(selected["external_id"])
        try:
            cloud.record_attempt(item_id, 1, "started")
        except Exception as exc:
            report["error"] = "cloud_attempt_start_failed_" + type(exc).__name__
            _report(output_report, report)
            return report

        try:
            response = publisher.send_media(media_path, validation["media_type"], caption)
        except Exception as exc:
            # The request might have reached Rubika before a transport error.
            try:
                cloud.record_attempt(item_id, 1, "uncertain", error_code="publisher_" + type(exc).__name__)
            except Exception:
                pass
            report["status"] = "uncertain"
            report["error"] = "rubika_delivery_uncertain_" + type(exc).__name__
            report["publishing"] = True
            _report(output_report, report)
            return report

        result = _parse_send_response(response)
        status = result.get("status")
        if isinstance(status, str) and status.upper() == "OK":
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            message_id = data.get("message_id") or result.get("message_id")
            try:
                cloud.record_attempt(item_id, 1, "sent", message_id=str(message_id) if message_id is not None else None)
            except Exception as exc:
                report["status"] = "published_ledger_update_failed"
                report["error"] = "sent_but_cloud_update_failed_" + type(exc).__name__
                report["publishing"] = True
                _report(output_report, report)
                return report
            report["status"] = "published"
            report["publishing"] = True
            report["rubika_message_id_recorded"] = message_id is not None
            # Keep the published local copy. The existing cleanup policy requires
            # age and durable sent-state checks before removing retained media.
            report["media_retained_for_age_based_cleanup"] = True
        elif isinstance(status, str) and status:
            try:
                cloud.record_attempt(item_id, 1, "failed", error_code="rubika_rejected_" + re.sub(r"[^A-Za-z0-9_-]", "", status.lower())[:40])
            except Exception:
                pass
            report["status"] = "rejected"
            report["error"] = "rubika_rejected_" + re.sub(r"[^A-Za-z0-9_-]", "", status.lower())[:40]
        else:
            try:
                cloud.record_attempt(item_id, 1, "uncertain", error_code="ambiguous_response")
            except Exception:
                pass
            report["status"] = "uncertain"
            report["error"] = "rubika_response_ambiguous"
            report["publishing"] = True
    except Exception as exc:
        report["error"] = "official_trial_" + type(exc).__name__ + ":" + re.sub(r"[^A-Za-z0-9_-]", "", str(exc))[:80]
    finally:
        _report(output_report, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Actually send one validated new official post to Rubika.")
    parser.add_argument("--limit", type=int, default=1, choices=[1], help="First trial is limited to exactly one post.")
    parser.add_argument("--report", type=Path, default=Path("artifacts/mob-official-trial-report.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/mob-official-media"))
    args = parser.parse_args()
    result = run_trial(
        publish=args.publish,
        limit=args.limit,
        output_report=args.report,
        output_dir=args.output_dir,
    )
    print(json.dumps({
        "status": result.get("status"),
        "publishing": result.get("publishing"),
        "candidate_count": result.get("candidate_count"),
        "selected": result.get("selected"),
        "error": result.get("error"),
        "report": str(args.report),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"validated_preview", "published", "nothing_new"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
