"""Shared Rubika caption builder for manual and automatic Mob X posts."""
from __future__ import annotations

import re


def clean_caption(value: object, limit: int = 5000) -> str:
    text = str(value or "").replace(chr(0), "").strip()
    text = re.sub(r"https?://\\S+", "", text)
    text = text.replace(chr(96), "")
    text = re.sub(r"[*_~]{1,3}", "", text)
    text = re.sub(r"[ \\t]+\\n", "\\n", text)
    return re.sub(r"\\n{3,}", "\\n\\n", text).strip()[:max(0, limit)]


def _safe_markdown_body(value: object, limit: int = 4000) -> str:
    """Keep caption content plain so source markup cannot inject Rubika metadata."""
    body = clean_caption(value, limit)
    body = body.replace("$", "＄")
    return re.sub(r"(?m)^>", "›", body)


def build_mob_x_caption(original_caption: object, translation: object = None) -> str:
    """Format an official Mob post with native Rubika bold and quote metadata."""
    source = _safe_markdown_body(original_caption, 5000)
    if not source:
        return "**ماب انترتینمنت در پیج خود پستی منتشر کرد.**"

    heading = "**ماب انترتینمنت در پیج خود پستی منتشر کرد و گفت:**"
    translated = _safe_markdown_body(translation, 4000)
    if not translated:
        return f"{heading}\\n\\n**{source}**"
    return f"{heading}\\n\\n**{source}**\\n\\n$**ترجمه متن:**\\n**{translated}**$"
