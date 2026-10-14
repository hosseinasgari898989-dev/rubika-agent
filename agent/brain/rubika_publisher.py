"""Media-type-aware adapter for the tested Rubka API surface.

Images and videos use Rubka's native media methods so they render inline in
Rubika instead of being delivered as generic file attachments.
Importing this module does not connect or publish.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


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

    def send_media(self, path: str | Path, media_type: str, caption: str | None = None) -> Any:
        local_path = Path(path)
        if not local_path.is_file() or local_path.stat().st_size <= 0:
            raise ValueError("media_file_missing_or_empty")
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
        if media_type == "audio":
            send_file = getattr(self.bot, "send_file", None)
            if not callable(send_file):
                raise RuntimeError("rubka_send_file_unavailable")
            return send_file(
                chat_id=self.channel_guid,
                path=str(local_path),
                caption=caption or None,
                file_name=name,
            )
        if media_type == "carousel":
            raise ValueError("carousel_requires_itemized_uploads")
        raise ValueError("unsupported_media_type")
