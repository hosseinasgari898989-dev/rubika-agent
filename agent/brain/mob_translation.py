"""Persian translation and link-free Mob caption formatting.

The post URL is retained as internal metadata only. No source link or invented
quote/translation is added to the outgoing Rubika caption.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable

MAX_TRANSLATION_CHARS = 4500
URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?", re.IGNORECASE)


def _strip_urls(value: str) -> str:
    value = URL_RE.sub("", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"[ \t]*\n[ \t]*", "\n", value)
    value = re.sub(r"\s+([,;:!?،؛؟])", r"\1", value)
    return value.strip()


def _translation_chunks(text: str, limit: int = 700) -> list[str]:
    """Split long source captions at sentence/paragraph boundaries when possible."""
    chunks: list[str] = []
    current = ""
    segments = [
        segment.strip()
        for segment in re.split(r"(?<=[.!?。！？])\s+|\n+", text)
        if segment.strip()
    ]
    for segment in segments:
        while len(segment) > limit:
            split_at = segment.rfind(" ", 0, limit + 1)
            if split_at <= 0:
                split_at = limit
            piece = segment[:split_at].strip()
            if piece:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(piece)
            segment = segment[split_at:].strip()
        if not segment:
            continue
        if current and len(current) + 1 + len(segment) <= limit:
            current += " " + segment
        else:
            if current:
                chunks.append(current)
            current = segment
    if current:
        chunks.append(current)
    return chunks


def translate_to_persian(
    text: str,
    *,
    translator_factory: Callable[..., Any] | None = None,
) -> str | None:
    """Translate real source text to Persian in bounded chunks; fail closed on any error."""
    raw_source = str(text or "").strip()
    if not raw_source:
        return None
    source = _strip_urls(raw_source)[:MAX_TRANSLATION_CHARS]
    if not source:
        return None

    # In the cloud trial, prefer the authenticated Cloudflare AI endpoint already
    # bound to this project's Worker. This avoids depending solely on Google's
    # public web-translation endpoint, which can be unavailable to CI runners.
    if translator_factory is None:
        base_url = os.getenv("AGENT_API_BASE_URL", "https://rubika-agent.h98609294.workers.dev")
        token = os.getenv("AGENT_API_TOKEN", "").strip()
        if token:
            try:
                from agent.brain.cloud_state import CloudState
                translated = CloudState(base_url, token=token, timeout_seconds=25).translate_official(source)
                if translated and translated.strip() and translated.casefold() != source.casefold():
                    return translated.strip()[:MAX_TRANSLATION_CHARS]
            except Exception:
                # Use the existing fallback below; never invent a translation.
                pass

    try:
        if translator_factory is None:
            from deep_translator import GoogleTranslator
            translator_factory = GoogleTranslator
        translator = translator_factory(source="auto", target="fa")
        translated_parts: list[str] = []
        for chunk in _translation_chunks(source):
            translated = translator.translate(chunk)
            if not isinstance(translated, str) or not translated.strip():
                return None
            translated_parts.append(re.sub(r"\s+", " ", translated).strip())
        translated_text = " ".join(part for part in translated_parts if part).strip()
        if not translated_text or translated_text.casefold() == source.casefold():
            return None
        return translated_text[:MAX_TRANSLATION_CHARS]
    except Exception:
        # Never substitute a guessed translation when one part could not be translated.
        return None


def format_mob_post(
    item: dict[str, Any],
    *,
    translation_provider: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Build the requested link-free post caption; original URL stays metadata-only."""
    original = str(item.get("original_caption") or item.get("caption") or "").strip()
    source_url = str(item.get("canonical_url") or item.get("url") or "").strip()
    source_name = str(item.get("source_name") or "Mob Entertainment").strip()

    translated: str | None = None
    if original:
        provider = translation_provider or translate_to_persian
        try:
            value = provider(original)
            if isinstance(value, str) and value.strip():
                translated = value.strip()[:MAX_TRANSLATION_CHARS]
        except Exception:
            translated = None

    display_original = _strip_urls(original)
    display_translation = _strip_urls(translated or "")
    lines: list[str] = []
    status = "no_caption"
    if display_original:
        lines.extend(["🎮 ماب انترتینمنت پستی منتشر کرد و گفت:", "", display_original])
        if display_translation:
            lines.extend(["", "📝 ترجمه فارسی:", display_translation])
            status = "translated"
        else:
            status = "unavailable"
    return {
        "caption": "\n".join(lines),
        "original_caption": original or None,
        "persian_translation": translated,
        "translation_status": status,
        "source_url": source_url or None,
        "source_name": source_name,
    }
