"""Template quiz drafts built only from wiki infobox fields (no AI needed)."""
from __future__ import annotations

import random
import re
from typing import Any

SPECS = (
    ("Debut", "debut", "اولین حضور «{name}» در کدام بخش بود؟"),
    ("Species", "species", "گونهٔ «{name}» چیست؟"),
    ("Role", "role", "نقش «{name}» در بازی چیست؟"),
    ("Status", "status", "وضعیت «{name}» چیست؟"),
    ("Voiced by", "voiced_by", "صداپیشهٔ «{name}» کیست؟"),
    ("Scent", "scent", "رایحهٔ «{name}» چیست؟"),
)
_UNKNOWN = re.compile(r"^(unknown|n/?a|none|\?+)\b", re.IGNORECASE)
_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


def plain(value: str) -> str:
    first = value.split("|")[0]
    first = re.sub(r"\[\w+\]", "", first)
    first = re.sub(r"\([^)]*\)", "", first)
    return re.sub(r"\s+", " ", first).strip(" :-")[:60]


def _answer(fields: dict[str, str], key: str) -> str | None:
    raw = fields.get(key)
    if not isinstance(raw, str):
        return None
    value = plain(raw)
    if len(value) < 2 or _UNKNOWN.match(value):
        return None
    return value


def build_drafts(characters: list[dict[str, Any]], count: int = 3, seed: str | None = None) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    pool = list(characters)
    rng.shuffle(pool)
    value_pools: dict[str, list[str]] = {}
    for key, _, _ in SPECS:
        found = {answer for answer in (_answer(c["fields"], key) for c in characters) if answer}
        value_pools[key] = sorted(found)
    drafts: list[dict[str, Any]] = []
    for character in pool:
        if len(drafts) >= count:
            break
        specs = list(SPECS)
        rng.shuffle(specs)
        for key, kind, template in specs:
            answer = _answer(character["fields"], key)
            if not answer:
                continue
            distractors = [v for v in value_pools[key] if v.casefold() != answer.casefold()]
            if len(distractors) < 2:
                continue
            options = rng.sample(distractors, 2) + [answer]
            rng.shuffle(options)
            name = _answer(character["fields"], "Full Name") or character["title"]
            drafts.append({
                "kind": kind,
                "title": character["title"],
                "url": character["url"],
                "question": template.format(name=name),
                "options": options,
                "answer_index": options.index(answer),
            })
            break
    return drafts[:count]


def _digits(number: int) -> str:
    return "".join(_DIGITS[int(ch)] for ch in str(number))


def render_message(draft: dict[str, Any], number: int) -> str:
    lines = [f"🧩 پیش‌نویس آزمون {_digits(number)} (منبع: ویکی Poppy Playtime)", "", draft["question"]]
    for index, option in enumerate(draft["options"], 1):
        lines.append(f"{_digits(index)}) {option}")
    lines += ["", f"✅ پاسخ: {_digits(draft['answer_index'] + 1)}", f"📎 {draft['url']} (CC BY-SA)"]
    return "\n".join(lines)
