"""Persian translation and link-free Mob caption formatting.

The post URL is retained as internal metadata only. No source link or invented
quote/translation is added to the outgoing Rubika caption.
"""
from __future__ import annotations

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


def translate_to_persian(
    text: str,
    *,
    translator_factory: Callable[..., Any] | None = None,
) -> str | None:
    """Translate one real source caption into Persian, returning None on failure."""
    source = str(text or "").strip()
    if not source:
        return None
    if len(source) > MAX_TRANSLATION_CHARS:
        source = source[:MAX_TRANSLATION_CHARS]

    try:
        if translator_factory is None:
            from deep_translator import GoogleTranslator
            translator_factory = GoogleTranslator
        translator = translator_factory(source="auto", target="fa")
        translated = translator.translate(source)
        if not isinstance(translated, str):
            return None
        translated = re.sub(r"\s+", " ", translated).strip()
        if not translated or translated.casefold() == source.casefold():
            return None
        return translated[:MAX_TRANSLATION_CHARS]
    except Exception:
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
