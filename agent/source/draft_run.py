"""Manual source draft run: reads the wiki, builds template quiz drafts, optionally sends them to the owner's PV only."""
from __future__ import annotations

import argparse
import datetime
import json
import os

from agent.source import quiz_templates, wiki_source


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
    report = {"ok": bool(drafts), "characters_read": len(characters), "drafts": len(drafts), "sent": 0, "send_failed": 0}
    if args.send and drafts:
        from agent.brain.owner_notify import send_owner_notification

        for message in messages:
            try:
                result = send_owner_notification(message, source="source-draft")
                if isinstance(result, dict) and result.get("ok") is False:
                    report["send_failed"] += 1
                else:
                    report["sent"] += 1
            except Exception:
                report["send_failed"] += 1
    report["preview"] = messages
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if drafts else 1


if __name__ == "__main__":
    raise SystemExit(main())
