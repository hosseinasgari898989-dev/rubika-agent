"""Optional Persian translation and source-attributed Mob post formatting.

Translation is best-effort: a provider failure never blocks a source post, and
the original caption/source link are always preserved. No AI-coded assumptions
or paraphrasing are added to the original text.
"""
from __future__ import annotations

import re
from typing import Any, Callable


MAX_TRANSLATION_CHARS = 4500


def translate_to_persian(
    text: str,
    *,
    translator_factory: Callable[..., Any] | None = None,
) -> str | None:
    """Translate one public caption into Persian, returning None on provider failure."""
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
        # Network/rate-limit/provider errors must not crash discovery or posting.
        return None


def format_mob_post(
    item: dict[str, Any],
    *,
    translation_provider: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Build a Persian Rubika caption without claiming Mob authored our translation."""
    original = str(item.get("original_caption") or item.get("caption") or "").strip()
    source_url = str(item.get("canonical_url") or item.get("url") or "").strip()
    source_name = str(item.get("source_name") or "Mob Entertainment").strip()
    title = str(item.get("title") or "").strip()

    if original:
        lines = [f"{source_name} پستی منتشر کرد و گفت:", "", original]
        translated = None
        provider = translation_provider or translate_to_persian
        try:
            translated = provider(original)
        except Exception:
            translated = None
        if isinstance(translated, str) and translated.strip():
            lines.extend(["", "ترجمه متن:", translated.strip()])
        else:
            lines.extend(["", "ترجمه متن فعلاً در دسترس نیست."])
    else:
        lines = [f"{source_name} یک پست جدید منتشر کرد."]
        if title:
            lines.extend(["", title])

    if source_url.startswith("https://"):
        lines.extend(["", f"منبع اصلی: {source_url}"])
    return {
        "caption": "\n".join(lines),
        "original_caption": original or None,
        "persian_translation": translated.strip() if original and isinstance(translated, str) and translated.strip() else None,
        "translation_status": "translated" if original and isinstance(translated, str) and translated.strip() else ("unavailable" if original else "no_caption"),
        "source_url": source_url or None,
        "source_name": source_name,
    }
