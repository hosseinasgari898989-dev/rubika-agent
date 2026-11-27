"""Manual source draft run: reads the wiki, builds template quiz drafts, optionally sends them to the owner's PV only."""
from __future__ import annotations

import argparse
import datetime
import json
import os
from typing import Any

from agent.source import quiz_templates, wiki_source


def _send_to_owner(draft: dict[str, Any], message: str) -> str | None:
    """Real Rubika Quiz poll first (the answer stays hidden until the user answers); plain text as fallback."""
    token = os.getenv("RUBIKA_TOKEN", "").strip()
    owner_id = os.getenv("RUBIKA_OWNER_CHAT_ID", "").strip()
    if not token or not owner_id:
        return None
    try:
        from agent.brain import owner_notify

        bot = owner_notify._make_rubika_bot(token)
        try:
            raw = bot._post("sendPoll", {
                "chat_id": owner_id,
                "question": draft["question"],
                "options": draft["options"],
                "type": "Quiz",
                "correct_option_index": draft["answer_index"],
                "explanation": quiz_templates.quiz_explanation(draft),
                "is_anonymous": True,
            })
            if owner_notify._rubika_ok(raw):
                return "quiz"
        except Exception:
            pass
        raw = bot.send_message(chat_id=owner_id, text=message)
        return "text" if owner_notify._rubika_ok(raw) else None
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args(argv)
    count = max(1, min(5, args.count))
    characters = wiki_source.load_characters(max_pages=30)
    seed = datetime.date.today().isoformat() + os.getenv("GITHUB_RUN_ID", "")
    drafts = quiz_templates.build_drafts(characters, count=count, seed=seed)
    messages = [quiz_templates.render_message(draft, index) for index, draft in enumerate(drafts, 1)]
    report: dict[str, Any] = {
        "ok": bool(drafts), "characters_read": len(characters), "drafts": len(drafts),
        "sent": 0, "quiz": 0, "text": 0, "send_failed": 0,
    }
    if args.send and drafts:
        for draft, message in zip(drafts, messages):
            result = _send_to_owner(draft, message)
            if result is None:
                report["send_failed"] += 1
            else:
                report["sent"] += 1
                report[result] += 1
    report["preview"] = messages
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if drafts else 1


if __name__ == "__main__":
    raise SystemExit(main())
