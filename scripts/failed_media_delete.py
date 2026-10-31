"""Delete Mega originals requested from the console.

Order is fixed: mega-rm first; the D1 row is removed by the Worker only after this runner reports success.
A file counts as removed only if mega-rm succeeded or a successful listing of failed/ no longer shows it.
Never prints captions, links or credentials.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import requests

BASE_URL = "https://rubika-agent.h98609294.workers.dev"
FAILED_DIR = "/Rubika-Agent/failed"
KEY_RE = re.compile(r"/Rubika-Agent/failed/[A-Za-z0-9._-]+")
ID_RE = re.compile(r"fm_[0-9a-f]{24}")


def _run(args, runner):
    return runner(args, capture_output=True, text=True, timeout=300)


def remove_remote(key: str, runner=subprocess.run) -> bool:
    """True only when the file is verifiably gone from Mega."""
    if not KEY_RE.fullmatch(key) or key.endswith(("/.", "/..")):
        return False
    try:
        if _run(["mega-rm", "-f", key], runner).returncode == 0:
            return True
        listing = _run(["mega-ls", FAILED_DIR], runner)
    except Exception:
        return False
    if listing.returncode != 0:  # cannot prove anything: keep the row, report failure
        return False
    names = {line.strip() for line in (listing.stdout or "").splitlines()}
    return key.rsplit("/", 1)[1] not in names


def process(post, runner=subprocess.run) -> dict:
    """post(path, payload) -> dict calls the Worker; returns counts only."""
    counts = {"pending": 0, "deleted": 0, "failed": 0}
    items = post("/api/console/failed-media/pending-delete", {}).get("items") or []
    counts["pending"] = len(items)
    for item in items:
        item_id = str(item.get("id") or "")
        if not ID_RE.fullmatch(item_id):
            counts["failed"] += 1
            continue
        ok = remove_remote(str(item.get("storage_key") or ""), runner)
        post("/api/console/failed-media/delete-result", {"id": item_id, "ok": ok})
        counts["deleted" if ok else "failed"] += 1
    return counts


def main() -> int:
    token = os.getenv("AGENT_API_TOKEN", "").strip()
    if len(token) < 32:
        print(json.dumps({"status": "failed", "error_code": "worker_credentials_missing_or_invalid"}))
        return 1
    base = (os.getenv("AGENT_API_BASE_URL") or BASE_URL).rstrip("/")
    session = requests.Session()

    def post(path: str, payload: dict) -> dict:
        response = session.post(
            base + path, json=payload, timeout=20,
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise RuntimeError("worker_response_invalid")
        return data

    try:
        counts = process(post)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_code": "failed_media_delete_error", "error_class": type(exc).__name__}))
        return 1
    counts["status"] = "done" if counts["failed"] == 0 else "partial"
    print(json.dumps(counts, sort_keys=True))
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
