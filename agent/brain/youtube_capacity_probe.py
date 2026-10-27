"""One-time, test-channel-only capacity probe for large 720p MP4 files.

The script measures local HTTP download capacity and progressively uploads test
MP4 files to the Rubika bot upload API. Only the largest upload-confirmed item is
temporarily sent to the guarded test channel; deletion is attempted immediately.
No production channel, AI provider, D1 API, or publishing queue is used.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

import requests

API_BASE = "https://botapi.rubika.ir/v3"
TEST_CHANNEL_SHA256 = "0c939688dd5536447389134ba514e9353a6ef97214de3c4f848adb53e1cd4ab8"
PROBE_SIZES_MIB = (16, 32, 64, 128, 256, 512, 768, 1024, 1536, 2048, 3072, 4096)
YOUTUBE_SAMPLE_URL = "https://www.youtube.com/shorts/VN5KSQ4didU"
MAX_YOUTUBE_METADATA_SECONDS = 60
MAX_YOUTUBE_DOWNLOAD_SECONDS = 300
MIB = 1024 * 1024
GIB = 1024 * MIB
MIN_FREE_RESERVE_BYTES = 2 * GIB
IO_CHUNK_BYTES = 1024 * 1024
API_TIMEOUT_SECONDS = (15, 60)
UPLOAD_TIMEOUT_SECONDS = 600


class ProbeError(RuntimeError):
    """Safe, non-sensitive capacity-probe error."""


def test_channel_is_authorized(channel_guid: str) -> bool:
    """Accept only the exact registered test destination without logging it."""
    value = str(channel_guid or "").strip()
    return bool(value) and hashlib.sha256(value.encode("utf-8")).hexdigest() == TEST_CHANNEL_SHA256


def probe_size_ladder_mib() -> tuple[int, ...]:
    """Progressive test sizes, not an upload/download policy or permanent cap."""
    return PROBE_SIZES_MIB


def _api_post(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call Bot API without surfacing the token-bearing URL or response body."""
    try:
        response = requests.post(
            f"{API_BASE}/{token}/{method}",
            json=payload,
            timeout=API_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return {"transport_ok": False, "http_status": None, "payload": None}
    try:
        data = response.json()
    except ValueError:
        data = None
    return {
        "transport_ok": True,
        "http_status": int(response.status_code),
        "payload": data if isinstance(data, dict) else None,
    }


def _create_sized_mp4(destination: Path, target_bytes: int) -> None:
    """Create a valid 720p MP4 test clip padded with a legal MP4 free box.

    The box is used only to make controlled upload sizes. The media stream stays
    a tiny 1280x720 clip; this is suitable for testing byte limits, not bitrate
    quality. Files are private temporary fixtures and are never committed.
    """
    if type(target_bytes) is not int or target_bytes < 1 * MIB or target_bytes > 2**32:
        raise ProbeError("probe_target_size_invalid")
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ProbeError("ffmpeg_or_ffprobe_missing")

    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=2",
        "-t", "2", "-an", "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "32", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-f", "mp4", str(destination),
    ]
    try:
        subprocess.run(command, check=True, timeout=120, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        raise ProbeError("synthetic_mp4_creation_failed") from None

    base_size = destination.stat().st_size
    box_size = target_bytes - base_size
    if box_size < 8 or box_size >= 2**32:
        raise ProbeError("synthetic_mp4_padding_size_invalid")
    with destination.open("ab") as handle:
        handle.write(struct.pack(">I4s", box_size, b"free"))
        handle.truncate(target_bytes)

    probe = _probe_mp4(destination)
    if probe.get("width") != 1280 or probe.get("height") != 720:
        raise ProbeError("synthetic_mp4_not_720p")
    if probe.get("duration_seconds", 0) <= 0:
        raise ProbeError("synthetic_mp4_duration_invalid")


def _probe_mp4(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ProbeError("ffprobe_missing")
    command = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,width,height",
        "-of", "json", str(path),
    ]
    try:
        result = subprocess.run(command, check=True, timeout=30, capture_output=True, text=True)
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ProbeError("synthetic_mp4_validation_failed") from None
    video = next(
        (s for s in payload.get("streams", []) if isinstance(s, dict) and s.get("codec_type") == "video"),
        None,
    )
    if not isinstance(video, dict):
        raise ProbeError("synthetic_mp4_video_stream_missing")
    try:
        duration = float(payload.get("format", {}).get("duration") or 0)
    except (AttributeError, TypeError, ValueError):
        duration = 0.0
    return {
        "width": video.get("width"),
        "height": video.get("height"),
        "duration_seconds": duration,
    }


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def _probe_youtube_download(root: Path) -> dict[str, Any]:
    """Download one known public Short with yt-dlp; this is a smoke test, not a maximum."""
    executable = shutil.which("yt-dlp")
    ffprobe = shutil.which("ffprobe")
    if not executable or not ffprobe:
        return {"status": "yt_dlp_or_ffprobe_missing"}

    try:
        from agent.brain.youtube_pipeline import validate_youtube_video_url
        url = validate_youtube_video_url(YOUTUBE_SAMPLE_URL)
    except (ImportError, ValueError):
        return {"status": "youtube_sample_url_invalid"}

    metadata_command = [
        executable,
        "--ignore-config",
        "--no-playlist",
        "--no-warnings",
        "--no-progress",
        "--skip-download",
        "--dump-single-json",
        "--socket-timeout", "15",
        "--retries", "1",
        "--",
        url,
    ]
    started = time.monotonic()
    try:
        meta = subprocess.run(
            metadata_command,
            capture_output=True,
            text=True,
            timeout=MAX_YOUTUBE_METADATA_SECONDS,
            check=False,
        )
        if meta.returncode != 0:
            return {
                "status": "youtube_metadata_failed",
                "seconds": round(time.monotonic() - started, 2),
            }
        payload = json.loads(meta.stdout)
        width, height = payload.get("width"), payload.get("height")
        if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
            return {"status": "youtube_dimensions_unavailable"}
        if width == height:
            maximum_width = maximum_height = 720
        elif width > height:
            maximum_width, maximum_height = 1280, 720
        else:
            maximum_width, maximum_height = 720, 1280
        estimated_size = payload.get("filesize") or payload.get("filesize_approx")
        if type(estimated_size) is not int or estimated_size <= 0:
            estimated_size = None
        estimated_duration = payload.get("duration")
        if type(estimated_duration) not in (int, float) or estimated_duration <= 0:
            estimated_duration = None
    except subprocess.TimeoutExpired:
        return {"status": "youtube_metadata_timeout"}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"status": "youtube_metadata_invalid"}

    selector = (
        f"bestvideo[width<={maximum_width}][height<={maximum_height}]+bestaudio/"
        f"best[width<={maximum_width}][height<={maximum_height}]"
    )
    output_template = str(root / "youtube-source-smoke.%(ext)s")
    command = [
        executable,
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "--continue",
        "--part",
        "--socket-timeout", "20",
        "--retries", "2",
        "--fragment-retries", "2",
        "--http-chunk-size", "10485760",
        "--format", selector,
        "--merge-output-format", "mp4",
        "--output", output_template,
        "--",
        url,
    ]
    try:
        download = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=MAX_YOUTUBE_DOWNLOAD_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "youtube_download_timeout",
            "estimated_size_bytes": estimated_size,
            "seconds": round(time.monotonic() - started, 2),
        }
    except OSError:
        return {"status": "yt_dlp_start_failed"}

    candidates = sorted(
        path for path in root.glob("youtube-source-smoke.*")
        if path.is_file() and path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".ts"}
    )
    if download.returncode != 0:
        for path in candidates:
            path.unlink(missing_ok=True)
        return {
            "status": "youtube_download_failed",
            "estimated_size_bytes": estimated_size,
            "seconds": round(time.monotonic() - started, 2),
        }
    if len(candidates) != 1:
        return {"status": "youtube_download_output_ambiguous"}
    output = candidates[0]
    try:
        media = _probe_mp4(output)
        actual_bytes = output.stat().st_size
    except (ProbeError, OSError):
        output.unlink(missing_ok=True)
        return {"status": "youtube_download_validation_failed"}
    output.unlink(missing_ok=True)
    if media["width"] > maximum_width or media["height"] > maximum_height:
        return {"status": "youtube_download_exceeded_720p_profile"}
    return {
        "status": "ok",
        "download_bytes": actual_bytes,
        "estimated_size_bytes": estimated_size,
        "duration_seconds": media["duration_seconds"],
        "width": media["width"],
        "height": media["height"],
        "seconds": round(time.monotonic() - started, 2),
        "notes": "One public Short only; this does not establish the maximum YouTube download size.",
    }


def _download_over_loopback(source: Path, destination: Path) -> dict[str, Any]:
    """Verify the runner can stream a file of this size without loading it in RAM."""
    handler = lambda *args, **kwargs: _QuietHandler(*args, directory=str(source.parent), **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    expected = source.stat().st_size
    transferred = 0
    started = time.monotonic()
    try:
        url = f"http://127.0.0.1:{server.server_port}/{quote(source.name)}"
        with urlopen(url, timeout=30) as response, destination.open("wb") as output:
            raw_length = response.headers.get("Content-Length")
            if raw_length is not None and int(raw_length) != expected:
                raise ProbeError("local_download_content_length_mismatch")
            while True:
                chunk = response.read(IO_CHUNK_BYTES)
                if not chunk:
                    break
                output.write(chunk)
                transferred += len(chunk)
        if transferred != expected or destination.stat().st_size != expected:
            raise ProbeError("local_download_size_mismatch")
        return {
            "ok": True,
            "bytes": transferred,
            "seconds": round(time.monotonic() - started, 2),
        }
    except ProbeError:
        raise
    except (OSError, URLError, TimeoutError, ValueError):
        raise ProbeError("local_download_failed") from None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _get_upload_url(token: str) -> str:
    result = _api_post(token, "requestSendFile", {"type": "Video"})
    payload = result.get("payload")
    data = payload.get("data") if isinstance(payload, dict) else None
    upload_url = data.get("upload_url") if isinstance(data, dict) else None
    if (
        result.get("transport_ok") is not True
        or result.get("http_status") != 200
        or not isinstance(payload, dict)
        or payload.get("status") != "OK"
        or not isinstance(upload_url, str)
    ):
        status = payload.get("status") if isinstance(payload, dict) else None
        safe_status = re.sub(r"[^A-Za-z0-9_-]", "", str(status or "request_rejected"))[:40]
        raise ProbeError("rubika_request_upload_url_" + safe_status.lower())
    parsed = urlsplit(upload_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ProbeError("rubika_upload_url_invalid")
    return upload_url


def _stream_upload(upload_url: str, media_path: Path, filename: str) -> str:
    """Stream multipart bytes over HTTPS instead of buffering a large file in RAM."""
    parsed = urlsplit(upload_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ProbeError("rubika_upload_url_invalid")
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query

    clean_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:120] or "capacity-probe.mp4"
    boundary = "RubikaAgentProbe" + uuid.uuid4().hex
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{clean_name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("ascii")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    total_length = len(prefix) + media_path.stat().st_size + len(suffix)
    conn: http.client.HTTPSConnection | None = None
    try:
        conn = http.client.HTTPSConnection(
            parsed.hostname, port=parsed.port or 443, timeout=UPLOAD_TIMEOUT_SECONDS
        )
        conn.putrequest("POST", target)
        host_header = parsed.hostname if parsed.port in (None, 443) else f"{parsed.hostname}:{parsed.port}"
        conn.putheader("Host", host_header)
        conn.putheader("User-Agent", "rubika-agent-capacity-probe/1.0")
        conn.putheader("Accept", "application/json")
        conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        conn.putheader("Content-Length", str(total_length))
        conn.endheaders()
        conn.send(prefix)
        with media_path.open("rb") as handle:
            while True:
                chunk = handle.read(IO_CHUNK_BYTES)
                if not chunk:
                    break
                conn.send(chunk)
        conn.send(suffix)
        response = conn.getresponse()
        raw = response.read(1024 * 1024)
        if response.status != 200:
            raise ProbeError("rubika_upload_http_" + str(response.status))
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProbeError("rubika_upload_invalid_response") from None
        data = payload.get("data") if isinstance(payload, dict) else None
        file_id = data.get("file_id") if isinstance(data, dict) else None
        if not isinstance(file_id, str) or not file_id.strip():
            status = payload.get("status") if isinstance(payload, dict) else None
            safe_status = re.sub(r"[^A-Za-z0-9_-]", "", str(status or "file_id_missing"))[:40]
            raise ProbeError("rubika_upload_" + safe_status.lower())
        return file_id
    except ProbeError:
        raise
    except (OSError, http.client.HTTPException, TimeoutError):
        raise ProbeError("rubika_upload_transport_failed") from None
    finally:
        if conn is not None:
            conn.close()


def _send_one_test_message(token: str, channel_guid: str, item: dict[str, Any]) -> dict[str, Any]:
    """Post one probe size, then delete it before the next size is attempted."""
    result = _api_post(token, "sendFile", {
        "chat_id": channel_guid,
        "file_id": item["file_id"],
        "text": f"🧪 آزمون موقت ظرفیت فایل ویدیویی ({item['size_mib']} MiB)؛ حذف خودکار پس از بررسی.",
        "disable_notification": True,
        "chat_keypad_type": "None",
    })
    payload = result.get("payload")
    if result.get("transport_ok") is not True:
        return {
            "status": "uncertain",
            "size_mib": item["size_mib"],
            "cleanup_confirmed": False,
            "error": "send_response_uncertain_do_not_retry",
        }
    if not isinstance(payload, dict):
        return {
            "status": "uncertain",
            "size_mib": item["size_mib"],
            "cleanup_confirmed": False,
            "error": "send_response_invalid_do_not_retry",
        }
    if int(result.get("http_status") or 0) >= 500:
        return {
            "status": "uncertain",
            "size_mib": item["size_mib"],
            "cleanup_confirmed": False,
            "error": "send_http_server_error_do_not_retry",
        }
    if payload.get("status") != "OK":
        detail = payload.get("status_det") or payload.get("error") or payload.get("error_code")
        safe_detail = re.sub(r"[^A-Za-z0-9_-]", "", str(detail or "api_rejected"))[:50].lower()
        return {
            "status": "rejected",
            "size_mib": item["size_mib"],
            "cleanup_confirmed": True,
            "error": "send_api_rejected_" + safe_detail,
        }

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    message_id = data.get("message_id") or payload.get("message_id")
    if not isinstance(message_id, str) or not message_id.strip():
        return {
            "status": "uncertain",
            "size_mib": item["size_mib"],
            "cleanup_confirmed": False,
            "error": "message_id_missing_do_not_retry",
        }

    deletion = _api_post(token, "deleteMessage", {
        "chat_id": channel_guid,
        "message_id": message_id,
    })
    delete_payload = deletion.get("payload")
    deleted = (
        deletion.get("transport_ok") is True
        and deletion.get("http_status") == 200
        and isinstance(delete_payload, dict)
        and delete_payload.get("status") == "OK"
    )
    return {
        "status": "sent_and_deleted" if deleted else "sent_delete_unconfirmed",
        "size_mib": item["size_mib"],
        "cleanup_confirmed": deleted,
        "error": None if deleted else "test_message_delete_unconfirmed",
    }

def _write_report(report: dict[str, Any]) -> None:
    output = Path("artifacts/youtube_capacity_probe_report.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [
        "## YouTube/Rubika size capacity probe",
        "",
        "This is a temporary test-channel measurement, not a permanent size limit.",
        "",
        "| Target size | Local download | Rubika upload | Test-channel post |",
        "|---:|---|---|---|",
    ]
    for row in report.get("sizes", []):
        lines.append(
            f"| {row.get('size_mib')} MiB | {row.get('download_status', 'not_run')} "
            f"| {row.get('upload_status', 'not_run')} | {row.get('channel_status', 'not_run')} |"
        )
    channel = report.get("channel_test", {})
    lines.extend([
        "",
        f"- YouTube source-download smoke: {report.get('youtube_download', {}).get('status', 'not_run')}",
        f"- YouTube smoke bytes: {report.get('youtube_download', {}).get('download_bytes', 'n/a')}",
        f"- Largest raw-upload success: {report.get('largest_upload_success_mib') or 'none'} MiB",
        f"- Largest size sent to test channel and deletion confirmed: {report.get('largest_channel_success_mib') or 'none'} MiB",
        f"- Last test-channel result: {channel.get('status', 'not_run')}",
        f"- Last test post size: {channel.get('size_mib') or 'n/a'} MiB",
        f"- Last test post deletion confirmed: {channel.get('cleanup_confirmed', 'n/a')}",
        f"- Test rung ceiling: {max(PROBE_SIZES_MIB)} MiB; this is the test plan ceiling, not a claimed API maximum.",
        "",
        "Raw failures are reduced to safe labels; tokens, channel IDs, upload URLs, and file IDs are omitted.",
    ])
    Path(summary_path).write_text(
        Path(summary_path).read_text(encoding="utf-8") + "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def run_capacity_probe() -> dict[str, Any]:
    token = str(os.getenv("RUBIKA_TOKEN") or "").strip()
    channel_guid = str(os.getenv("RUBIKA_CHANNEL_GUID") or "").strip()
    if not token or len(token) < 16:
        raise ProbeError("rubika_token_missing_or_invalid")
    if not test_channel_is_authorized(channel_guid):
        raise ProbeError("rubika_destination_not_registered_test_channel")

    identity = _api_post(token, "getMe", {})
    identity_payload = identity.get("payload")
    if (
        identity.get("transport_ok") is not True
        or identity.get("http_status") != 200
        or not isinstance(identity_payload, dict)
        or identity_payload.get("status") != "OK"
    ):
        raise ProbeError("rubika_bot_authentication_failed")

    report: dict[str, Any] = {
        "ok": False,
        "status": "running",
        "test_channel_only": True,
        "test_sizes_mib": list(PROBE_SIZES_MIB),
        "sizes": [],
        "largest_upload_success_mib": None,
        "largest_channel_success_mib": None,
        "youtube_download": {"status": "not_run"},
        "channel_test": {"status": "not_run", "cleanup_confirmed": None},
        "notes": [
            "Test sizes are exploratory rungs, not policy caps.",
            "The local HTTP transfer checks runner download handling, not YouTube's own availability or limits.",
        ],
    }
    with tempfile.TemporaryDirectory(prefix="rubika-agent-size-probe-") as folder:
        root = Path(folder)
        report["youtube_download"] = _probe_youtube_download(root)
        for size_mib in PROBE_SIZES_MIB:
            target_bytes = size_mib * MIB
            free = shutil.disk_usage(root).free
            row: dict[str, Any] = {"size_mib": size_mib, "target_bytes": target_bytes}
            if free < target_bytes * 2 + MIN_FREE_RESERVE_BYTES:
                row.update(
                    download_status="skipped_insufficient_runner_disk",
                    upload_status="not_run",
                    channel_status="not_run",
                )
                report["sizes"].append(row)
                break

            source = root / f"capacity-source-{size_mib}mib.mp4"
            downloaded = root / f"capacity-downloaded-{size_mib}mib.mp4"
            try:
                _create_sized_mp4(source, target_bytes)
                download_result = _download_over_loopback(source, downloaded)
                row.update(
                    download_status="ok",
                    download_bytes=download_result["bytes"],
                    download_seconds=download_result["seconds"],
                )
            except ProbeError as exc:
                row.update(download_status=exc.args[0], upload_status="not_run", channel_status="not_run")
                report["sizes"].append(row)
                source.unlink(missing_ok=True)
                downloaded.unlink(missing_ok=True)
                break
            finally:
                source.unlink(missing_ok=True)

            try:
                upload_url = _get_upload_url(token)
                file_id = _stream_upload(upload_url, downloaded, f"capacity-probe-{size_mib}mib.mp4")
            except ProbeError as exc:
                row.update(upload_status=exc.args[0], channel_status="not_run")
                report["sizes"].append(row)
                downloaded.unlink(missing_ok=True)
                break
            else:
                row["upload_status"] = "ok"
                report["largest_upload_success_mib"] = size_mib
                row["channel_status"] = "running"
                channel_result = _send_one_test_message(
                    token, channel_guid, {"size_mib": size_mib, "file_id": file_id}
                )
                row["channel_status"] = channel_result.get("status", "unknown")
                row["channel_cleanup_confirmed"] = channel_result.get("cleanup_confirmed")
                report["channel_test"] = channel_result
                if channel_result.get("status") == "sent_and_deleted":
                    report["largest_channel_success_mib"] = size_mib
                report["sizes"].append(row)
                downloaded.unlink(missing_ok=True)
                if channel_result.get("status") != "sent_and_deleted":
                    # Do not accumulate test posts or retry an ambiguous send.
                    break

    report["ok"] = report["largest_channel_success_mib"] is not None
    if report["channel_test"].get("status") == "sent_delete_unconfirmed":
        report["status"] = "completed_cleanup_unconfirmed"
    elif report["ok"]:
        report["status"] = "completed"
    else:
        report["status"] = "no_channel_size_confirmed"
    _write_report(report)
    return report


def main() -> int:
    try:
        report = run_capacity_probe()
    except ProbeError as exc:
        # Safe fixed labels only; no exception strings from credential-bearing HTTP calls.
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "ok": report.get("ok"),
        "status": report.get("status"),
        "youtube_download_status": report.get("youtube_download", {}).get("status"),
        "youtube_download_bytes": report.get("youtube_download", {}).get("download_bytes"),
        "largest_upload_success_mib": report.get("largest_upload_success_mib"),
        "largest_channel_success_mib": report.get("largest_channel_success_mib"),
        "channel_test_status": report.get("channel_test", {}).get("status"),
        "cleanup_confirmed": report.get("channel_test", {}).get("cleanup_confirmed"),
        "sizes": [
            {
                "size_mib": row.get("size_mib"),
                "download": row.get("download_status"),
                "upload": row.get("upload_status"),
                "channel": row.get("channel_status"),
            }
            for row in report.get("sizes", [])
        ],
    }, ensure_ascii=False, sort_keys=True))
    if not report.get("ok"):
        return 1
    if report.get("status") == "completed_cleanup_unconfirmed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
