"""Manual Gemini API smoke test: lists usable Flash models and runs two tiny text calls."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

API = "https://generativelanguage.googleapis.com/v1beta"
EXCLUDED = ("image", "tts", "live", "embedding", "native-audio", "robotics", "computer-use", "veo", "imagen", "gemma")


def _request(method: str, path: str, key: str, body: dict[str, Any] | None = None, timeout: int = 30) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
            return response.status, payload if isinstance(payload, dict) else {}
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8") or "{}")
        except Exception:
            payload = {}
        return error.code, payload if isinstance(payload, dict) else {}
    except Exception as error:
        return 0, {"error": {"message": type(error).__name__}}


def _candidate_models(key: str) -> tuple[int, list[str]]:
    status, payload = _request("GET", "/models?pageSize=200", key)
    names: list[str] = []
    for item in (payload.get("models") or []) if status == 200 else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).removeprefix("models/")
        lowered = name.lower()
        methods = item.get("supportedGenerationMethods") or []
        if "generateContent" in methods and "flash" in lowered and not any(word in lowered for word in EXCLUDED):
            names.append(name)
    names.sort(key=lambda value: (0 if "lite" in value.lower() else 1, value))
    return status, names


def _text(payload: dict[str, Any]) -> str:
    try:
        return str(payload["candidates"][0]["content"]["parts"][0]["text"]).strip()
    except Exception:
        return ""


def _generate(key: str, model: str, prompt: str) -> tuple[int, dict[str, Any]]:
    return _request("POST", f"/models/{model}:generateContent", key, {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 200, "temperature": 0},
    })


def run_smoke() -> dict[str, Any]:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    report: dict[str, Any] = {
        "key_present": bool(key), "ok": False, "list_status": None,
        "models_listed": 0, "tested": [], "first_working_model": None,
    }
    if not key:
        return report
    list_status, names = _candidate_models(key)
    report["list_status"] = list_status
    report["models_listed"] = len(names)
    for name in names[:3]:
        status, payload = _generate(key, name, "Reply with exactly one word: OK")
        entry: dict[str, Any] = {"model": name, "status": status, "ok": False}
        text = _text(payload)
        usage = payload.get("usageMetadata") if isinstance(payload.get("usageMetadata"), dict) else {}
        entry["tokens"] = {k: usage.get(k) for k in ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")}
        if status == 200 and text:
            entry["ok"] = True
            entry["text"] = text[:40]
            t_status, t_payload = _generate(key, name, "Translate to Persian in one short sentence: New edit posted today.")
            entry["translation_status"] = t_status
            entry["translation"] = _text(t_payload)[:120]
        else:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            entry["error_status"] = str(error.get("status", ""))[:40]
            entry["error"] = str(error.get("message", ""))[:120].replace(key, "<redacted>")
        report["tested"].append(entry)
        if entry["ok"]:
            report["first_working_model"] = name
            report["ok"] = True
            break
    return report


def main() -> int:
    report = run_smoke()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
