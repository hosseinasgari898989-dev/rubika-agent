"""Non-publishing environment checks for the first cloud milestone."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def check_cloud_health(base_url: str) -> dict[str, object]:
    url = base_url.rstrip("/") + "/health"
    request = urllib.request.Request(url, headers={"User-Agent": "rubika-agent-preflight/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if response.status != 200 or payload.get("ok") is not True:
                raise RuntimeError("health endpoint returned an unhealthy response")
            return {"ok": True, "url": url, "database": payload.get("database", "unknown")}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        return {"ok": False, "url": url, "error": type(exc).__name__}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "self-test"
    if mode not in {"self-test", "cloud-health"}:
        print("Unsupported mode. Use self-test or cloud-health.")
        return 2

    if mode == "self-test":
        # Do not print token values or require publishing credentials during bootstrap.
        print(json.dumps({"ok": True, "mode": mode, "publishing": False}, sort_keys=True))
        return 0

    base_url = os.getenv("CONTROL_API_URL", "").strip()
    if not base_url.startswith("https://"):
        print(json.dumps({"ok": False, "error": "CONTROL_API_URL must be an HTTPS URL"}))
        return 2
    result = check_cloud_health(base_url)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
