"""Manual read-only probe: which infobox fields do Poppy Playtime wiki character pages have? (stdlib only)"""
from __future__ import annotations

import collections
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

BASE = "https://poppy-playtime.fandom.com"
USER_AGENT = "Rubika-Agent-Wiki-Probe/1.0 (read-only, low volume; contact: repository owner)"
VOID = {"br", "img", "hr", "wbr", "input", "meta", "link", "source"}
MAX_PAGES = 12


class InfoboxParser(HTMLParser):
    """Collects (label, value) pairs from Fandom portable infobox markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, str]] = []
        self._kind: str | None = None
        self._depth = 0
        self._buf: list[str] = []
        self._label: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID:
            return
        classes = (dict(attrs).get("class") or "").split()
        if self._kind is None:
            if "pi-data-label" in classes:
                self._kind, self._depth, self._buf = "label", 1, []
            elif "pi-data-value" in classes:
                self._kind, self._depth, self._buf = "value", 1, []
        else:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID or self._kind is None:
            return
        self._depth -= 1
        if self._depth == 0:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if self._kind == "label":
                self._label = text
            elif self._label:
                self.rows.append((self._label, text))
                self._label = None
            self._kind = None

    def handle_data(self, data: str) -> None:
        if self._kind is not None:
            self._buf.append(data)


def _get(params: dict[str, str], timeout: int = 30) -> tuple[int, Any]:
    url = BASE + "/api.php?" + urllib.parse.urlencode({**params, "format": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read(3_000_000).decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        return error.code, {}
    except Exception as error:
        return 0, {"error": type(error).__name__}


def parse_infobox(markup: str) -> list[tuple[str, str]]:
    parser = InfoboxParser()
    parser.feed(markup)
    return parser.rows


def run_probe(sleep_seconds: float = 0.5) -> dict[str, Any]:
    report: dict[str, Any] = {"ok": False, "pages": [], "label_frequency": {}}
    status, payload = _get({"action": "query", "list": "categorymembers", "cmtitle": "Category:Characters", "cmnamespace": "0", "cmlimit": "40"})
    members = payload.get("query", {}).get("categorymembers", []) if isinstance(payload, dict) else []
    titles = [m["title"] for m in members if isinstance(m, dict) and isinstance(m.get("title"), str)]
    report["category_status"] = status
    report["category_count"] = len(titles)
    frequency: collections.Counter[str] = collections.Counter()
    for title in titles:
        if len(report["pages"]) >= MAX_PAGES:
            break
        page_status, page = _get({"action": "parse", "page": title, "prop": "text", "section": "0", "redirects": "1", "disableeditsection": "1"})
        markup = page.get("parse", {}).get("text", {}).get("*", "") if isinstance(page, dict) and isinstance(page.get("parse"), dict) else ""
        rows = parse_infobox(markup) if isinstance(markup, str) else []
        if len(rows) >= 3:
            report["pages"].append({
                "title": title[:40], "status": page_status,
                "fields": {label[:30]: value[:40] for label, value in rows[:14]},
            })
            frequency.update(label[:30] for label, _ in rows)
        if sleep_seconds:
            time.sleep(sleep_seconds)
    report["label_frequency"] = dict(frequency.most_common(25))
    report["ok"] = len(report["pages"]) >= 3
    return report


def main() -> int:
    report = run_probe()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
