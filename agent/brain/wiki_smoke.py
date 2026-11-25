"""Manual wiki read smoke test (stdlib only, read-only): MediaWiki API of the Poppy Playtime wiki."""
from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASES = ("https://poppy-playtime.fandom.com", "https://poppy-playtime.fandom.com")
KNOWN_TITLES = ("Huggy Wuggy", "Mommy Long Legs", "Kissy Missy", "Poppy Playtime")
USER_AGENT = "Rubika-Agent-Wiki-Smoke/1.0 (read-only, low volume; contact: repository owner)"


def _get(base: str, params: dict[str, str], timeout: int = 30) -> tuple[int, Any]:
    url = base + "/api.php?" + urllib.parse.urlencode({**params, "format": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read(2_000_000).decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        return error.code, {}
    except Exception as error:
        return 0, {"error": type(error).__name__}


def _strip(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def _extract(base: str, title: str) -> tuple[int, str, str]:
    status, payload = _get(base, {"action": "query", "prop": "extracts", "exintro": "1", "explaintext": "1", "redirects": "1", "titles": title})
    pages = payload.get("query", {}).get("pages", {}) if isinstance(payload, dict) else {}
    text = ""
    for page in pages.values() if isinstance(pages, dict) else []:
        if isinstance(page, dict) and isinstance(page.get("extract"), str):
            text = page["extract"]
    if len(text) > 50:
        return status, "extracts", text
    status, payload = _get(base, {"action": "parse", "page": title, "prop": "text", "section": "0", "redirects": "1", "disableeditsection": "1"})
    raw = payload.get("parse", {}).get("text", {}).get("*", "") if isinstance(payload, dict) and isinstance(payload.get("parse"), dict) else ""
    return status, "parse", _strip(raw) if isinstance(raw, str) else ""


def run_smoke() -> dict[str, Any]:
    report: dict[str, Any] = {"ok": False, "base": None, "method": None, "tested": []}
    for base in BASES:
        entry: dict[str, Any] = {"base": base, "pages": []}
        status, payload = _get(base, {"action": "query", "list": "categorymembers", "cmtitle": "Category:Characters", "cmnamespace": "0", "cmlimit": "10"})
        members = payload.get("query", {}).get("categorymembers", []) if isinstance(payload, dict) else []
        titles = [m["title"] for m in members if isinstance(m, dict) and isinstance(m.get("title"), str)]
        entry["category_status"] = status
        entry["category_titles"] = [t[:40] for t in titles[:6]]
        for title in list(KNOWN_TITLES) + titles[:2]:
            page_status, method, text = _extract(base, title)
            entry["pages"].append({"title": title[:40], "method": method, "status": page_status, "chars": len(text)})
            if len(text) > 50:
                entry["ok"] = True
                report.update(ok=True, base=base, method=method)
                break
        report["tested"].append(entry)
        if report["ok"]:
            break
    return report


def main() -> int:
    report = run_smoke()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
