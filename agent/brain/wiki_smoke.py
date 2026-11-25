"""Manual wiki read smoke test (stdlib only, read-only): MediaWiki API of the Poppy Playtime wiki."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASES = ("https://poppy-playtime.fandom.com", "https://poppy-playtime.fandom.com")
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


def run_smoke() -> dict[str, Any]:
    report: dict[str, Any] = {"ok": False, "base": None, "tested": []}
    for base in BASES:
        entry: dict[str, Any] = {"base": base}
        status, payload = _get(base, {"action": "query", "list": "categorymembers", "cmtitle": "Category:Characters", "cmlimit": "10"})
        members = payload.get("query", {}).get("categorymembers", []) if isinstance(payload, dict) else []
        entry["category_status"] = status
        entry["category_members"] = len(members) if isinstance(members, list) else 0
        titles = [m.get("title") for m in members if isinstance(m, dict) and isinstance(m.get("title"), str)][:1] or ["Huggy Wuggy"]
        status, payload = _get(base, {"action": "query", "prop": "extracts", "exintro": "1", "explaintext": "1", "redirects": "1", "titles": titles[0]})
        pages = payload.get("query", {}).get("pages", {}) if isinstance(payload, dict) else {}
        extract = ""
        for page in pages.values() if isinstance(pages, dict) else []:
            if isinstance(page, dict) and isinstance(page.get("extract"), str):
                extract = page["extract"]
        entry["extract_status"] = status
        entry["sample_title"] = titles[0][:60]
        entry["extract_chars"] = len(extract)
        entry["ok"] = status == 200 and len(extract) > 50
        report["tested"].append(entry)
        if entry["ok"]:
            report["ok"] = True
            report["base"] = base
            break
    return report


def main() -> int:
    report = run_smoke()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
