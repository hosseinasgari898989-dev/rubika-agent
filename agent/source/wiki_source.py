"""Read-only Poppy Playtime wiki access (MediaWiki API, parse method) and infobox normalization. Stdlib only."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

BASE = "https://poppy-playtime.fandom.com"
USER_AGENT = "Rubika-Agent-Source/1.0 (read-only, low volume; contact: repository owner)"
VOID = {"br", "img", "hr", "wbr", "input", "meta", "link", "source"}
SEPARATOR = " | "


def clean_cell(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    parts = [part.strip() for part in text.split("|")]
    return SEPARATOR.join(part for part in parts if part)


class InfoboxParser(HTMLParser):
    """Collects (label, value) pairs from Fandom portable infobox markup; <br>/<li> become separators, <sup> is dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, str]] = []
        self._kind: str | None = None
        self._depth = 0
        self._buf: list[str] = []
        self._label: str | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if self._kind is not None:
            if tag in ("br", "li"):
                self._buf.append(SEPARATOR)
            if tag in VOID:
                return
            self._depth += 1
            if tag == "sup":
                self._skip += 1
            return
        if tag in VOID:
            return
        if "pi-data-label" in classes:
            self._kind, self._depth, self._buf, self._skip = "label", 1, [], 0
        elif "pi-data-value" in classes:
            self._kind, self._depth, self._buf, self._skip = "value", 1, [], 0

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID or self._kind is None:
            return
        if tag == "sup" and self._skip:
            self._skip -= 1
        self._depth -= 1
        if self._depth == 0:
            text = clean_cell("".join(self._buf))
            if self._kind == "label":
                self._label = text
            elif self._label:
                self.rows.append((self._label, text))
                self._label = None
            self._kind = None

    def handle_data(self, data: str) -> None:
        if self._kind is not None and not self._skip:
            self._buf.append(data)


def parse_infobox(markup: str) -> list[tuple[str, str]]:
    parser = InfoboxParser()
    parser.feed(markup)
    return parser.rows


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


def category_titles(limit: int = 60) -> list[str]:
    _, payload = _get({"action": "query", "list": "categorymembers", "cmtitle": "Category:Characters", "cmnamespace": "0", "cmlimit": str(limit)})
    members = payload.get("query", {}).get("categorymembers", []) if isinstance(payload, dict) else []
    return [m["title"] for m in members if isinstance(m, dict) and isinstance(m.get("title"), str)]


def fetch_fields(title: str) -> dict[str, str]:
    _, payload = _get({"action": "parse", "page": title, "prop": "text", "section": "0", "redirects": "1", "disableeditsection": "1"})
    markup = payload.get("parse", {}).get("text", {}).get("*", "") if isinstance(payload, dict) and isinstance(payload.get("parse"), dict) else ""
    fields: dict[str, str] = {}
    for label, value in parse_infobox(markup) if isinstance(markup, str) else []:
        if value and label[:40] not in fields:
            fields[label[:40]] = value[:300]
    return fields


def is_character(fields: dict[str, str]) -> bool:
    return all(key in fields for key in ("Species", "Status", "Debut")) and "Type" not in fields


def page_url(title: str) -> str:
    return BASE + "/wiki/" + urllib.parse.quote(title.replace(" ", "_"))


def load_characters(max_pages: int = 30, sleep_seconds: float = 0.4) -> list[dict[str, Any]]:
    characters: list[dict[str, Any]] = []
    for title in category_titles(limit=60):
        if len(characters) >= max_pages:
            break
        fields = fetch_fields(title)
        if is_character(fields):
            characters.append({"title": title, "url": page_url(title), "fields": fields})
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return characters
