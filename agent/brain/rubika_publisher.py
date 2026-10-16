"""Media-type-aware Rubka publisher with safe Markdown metadata for captions.

The pinned Rubka package supports Markdown-to-Rubika metadata parsing, but its
high-level media methods omit the metadata field when they call sendFile. For
formatted captions we therefore upload once, then send the same file_id with
the parsed text and metadata payload. Plain captions continue through the
library's native media methods.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


MARKDOWN_SIGNAL = re.compile(r"\*\*.+?\*\*|__(.+?)__|--(.+?)--|~~(.+?)~~|\|\|.+?\|\||`.+?`|^>.+?$", re.MULTILINE | re.DOTALL)


def _escape_source_markup(value: str) -> str:
    """Prevent source-caption text from injecting formatting metadata."""
    for marker in ("**", "__", "~~", "||"):
        value = value.replace(marker, marker[0] + "\u200b" + marker[1])
    value = value.replace("`", "ˋ").replace("$", "＄")
    return re.sub(r"(?m)^>", "›", value)


def _decorate_official_mob_caption(caption: str) -> str:
    """Use small bold/quote decorations for official Mob news only."""
    lines = caption.splitlines()
    if not lines or lines[0].strip() != "🎮 ماب انترتینمنت پستی منتشر کرد و گفت:":
        return caption

    translation_index = next(
        (i for i, line in enumerate(lines) if line.strip() == "📝 ترجمه فارسی:"),
        -1,
    )
    if translation_index < 0:
        return caption

    source_lines = lines[2:translation_index]
    while source_lines and not source_lines[0].strip():
        source_lines.pop(0)
    while source_lines and not source_lines[-1].strip():
        source_lines.pop()
    translation_lines = lines[translation_index + 1:]
    while translation_lines and not translation_lines[0].strip():
        translation_lines.pop(0)

    safe_source = [_escape_source_markup(line) for line in source_lines]
    quoted_source = "\n".join("> " + line if line else ">" for line in safe_source)
    safe_translation = [_escape_source_markup(line) for line in translation_lines]
    output = ["**🎮 ماب انترتینمنت پستی منتشر کرد و گفت:**", ""]
    if quoted_source:
        output.extend([quoted_source, ""])
    output.append("**📝 ترجمه فارسی:**")
    output.extend(safe_translation)
    return "\n".join(output)


def _parse_formatted_caption(caption: str) -> dict[str, Any] | None:
    """Return a valid Rubika metadata payload only when the caption uses markup."""
    if not caption or not MARKDOWN_SIGNAL.search(caption):
        return None
    from rubka.metadata import Track_parsed

    parsed = Track_parsed().parse(caption, parse_mode="MARKDOWN")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("text"), str):
        raise RuntimeError("rubka_caption_parse_failed")
    metadata = parsed.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("meta_data_parts"), list):
        raise RuntimeError("rubka_caption_metadata_missing")
    parts = metadata["meta_data_parts"]
    if not parts or any(not isinstance(part, dict) for part in parts):
        raise RuntimeError("rubka_caption_metadata_invalid")
    text = parsed["text"]
    if not text.strip() or len(text) > 3800:
        raise ValueError("rubka_caption_text_out_of_range")
    return {"text": text, "metadata": metadata}


class RubikaPublisher:
    def __init__(self, token: str, channel_guid: str, *, bot_factory: Any | None = None) -> None:
        if not token.strip():
            raise ValueError("missing_RUBIKA_TOKEN")
        if not channel_guid.strip():
            raise ValueError("missing_RUBIKA_CHANNEL_GUID")
        self.channel_guid = channel_guid
        if bot_factory is None:
            from rubka import Bot
            bot_factory = Bot
        self.bot = bot_factory(token=token, timeout=30, show_progress=True)

    def _send_formatted_media(self, path: Path, media_type: str, caption: dict[str, Any]) -> Any:
        post = getattr(self.bot, "_post", None)
        get_upload_url = getattr(self.bot, "get_upload_url", None)
        upload_media_file = getattr(self.bot, "upload_media_file", None)
        if not callable(post) or not callable(get_upload_url) or not callable(upload_media_file):
            raise RuntimeError("rubka_formatted_media_api_unavailable")

        rubka_type = {"image": "Image", "video": "Video", "audio": "File"}.get(media_type)
        if rubka_type is None:
            raise ValueError("unsupported_media_type")
        upload_url = get_upload_url(rubka_type)
        if not isinstance(upload_url, str) or not upload_url.startswith("https://"):
            raise RuntimeError("rubka_media_upload_url_invalid")
        file_id = upload_media_file(upload_url, path.name, path)
        if not isinstance(file_id, str) or not file_id.strip():
            raise RuntimeError("rubka_media_upload_receipt_missing")

        payload = {
            "chat_id": self.channel_guid,
            "file_id": file_id,
            "text": caption["text"],
            "metadata": caption["metadata"],
            "disable_notification": False,
            "chat_keypad_type": "None",
        }
        # Deliberately send once: never retry an ambiguous media send and duplicate it.
        return post("sendFile", payload)

    def send_media(self, path: str | Path, media_type: str, caption: str | None = None) -> Any:
        local_path = Path(path)
        if not local_path.is_file() or local_path.stat().st_size <= 0:
            raise ValueError("media_file_missing_or_empty")
        if media_type not in {"image", "video", "audio"}:
            if media_type == "carousel":
                raise ValueError("carousel_requires_itemized_uploads")
            raise ValueError("unsupported_media_type")

        outgoing_caption = _decorate_official_mob_caption(caption or "")
        formatted_caption = _parse_formatted_caption(outgoing_caption)
        if formatted_caption is not None:
            return self._send_formatted_media(local_path, media_type, formatted_caption)

        name = local_path.name
        if media_type == "image":
            send_image = getattr(self.bot, "send_image", None)
            if not callable(send_image):
                raise RuntimeError("rubka_send_image_unavailable")
            return send_image(
                chat_id=self.channel_guid,
                path=str(local_path),
                text=caption or None,
                file_name=name,
            )
        if media_type == "video":
            return self.bot.send_video(
                chat_id=self.channel_guid,
                path=str(local_path),
                text=caption or None,
                file_name=name,
            )
        send_file = getattr(self.bot, "send_file", None)
        if not callable(send_file):
            raise RuntimeError("rubka_send_file_unavailable")
        return send_file(
            chat_id=self.channel_guid,
            path=str(local_path),
            caption=caption or None,
            file_name=name,
        )
