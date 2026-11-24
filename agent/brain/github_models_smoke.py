"""Manual GitHub Models smoke test: one tiny text call and one Persian JSON quiz call (stdlib only)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

BASE = "https://models.github.ai"
PREFERRED = (
    "openai/gpt-4.1-mini", "openai/gpt-4o-mini", "openai/gpt-4.1", "openai/gpt-4o",
    "meta/llama-3.3-70b-instruct", "deepseek/deepseek-v3-0324", "mistral-ai/mistral-small-2503",
)


def _request(method: str, url: str, token: str, body: dict[str, Any] | None = None, timeout: int = 45) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read().decode("utf-8") or "{}")
        except Exception:
            return error.code, {}
    except Exception as error:
        return 0, {"error": {"message": type(error).__name__}}


def _catalog_ids(token: str) -> tuple[int, list[str]]:
    status, payload = _request("GET", BASE + "/catalog/models", token)
    ids: list[str] = []
    if status == 200 and isinstance(payload, list):
        ids = [item["id"] for item in payload if isinstance(item, dict) and isinstance(item.get("id"), str)]
    return status, ids


def _candidates(ids: list[str]) -> list[str]:
    if not ids:
        return list(PREFERRED[:3])
    ordered = [model for model in PREFERRED if model in ids]
    ordered += [model for model in ids if model not in ordered][:2]
    return ordered[:3]


def _chat(token: str, model: str, prompt: str, json_mode: bool = False) -> tuple[int, Any]:
    body: dict[str, Any] = {
        "model": model, "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 300, "temperature": 0,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    return _request("POST", BASE + "/inference/chat/completions", token, body)


def _text(payload: Any) -> str:
    try:
        return str(payload["choices"][0]["message"]["content"]).strip()
    except Exception:
        return ""


def _error(payload: Any, token: str) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    return str(message or "")[:160].replace(token, "<redacted>")


def run_smoke() -> dict[str, Any]:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    report: dict[str, Any] = {
        "token_present": bool(token), "ok": False, "catalog_status": None,
        "catalog_models": 0, "tested": [], "first_working_model": None,
    }
    if not token:
        return report
    catalog_status, ids = _catalog_ids(token)
    report["catalog_status"] = catalog_status
    report["catalog_models"] = len(ids)
    for model in _candidates(ids):
        status, payload = _chat(token, model, "Reply with exactly one word: OK")
        entry: dict[str, Any] = {"model": model, "status": status, "ok": False}
        usage = payload.get("usage") if isinstance(payload, dict) and isinstance(payload.get("usage"), dict) else {}
        entry["tokens"] = {key: usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        text = _text(payload)
        if status == 200 and text:
            entry["ok"] = True
            entry["text"] = text[:40]
            q_status, q_payload = _chat(
                token, model,
                "Write one hard but fair multiple-choice trivia question about the game Poppy Playtime in Persian "
                "with exactly 3 short options. Reply only as JSON: "
                "{\"question\": string, \"options\": [string, string, string], \"answer_index\": integer}",
                json_mode=True,
            )
            entry["quiz_status"] = q_status
            try:
                quiz = json.loads(_text(q_payload))
                entry["quiz_json_ok"] = (
                    isinstance(quiz, dict) and isinstance(quiz.get("options"), list)
                    and len(quiz["options"]) == 3 and isinstance(quiz.get("answer_index"), int)
                )
                entry["quiz_question"] = str(quiz.get("question", ""))[:120] if isinstance(quiz, dict) else ""
            except Exception:
                entry["quiz_json_ok"] = False
            if q_status != 200:
                entry["quiz_error"] = _error(q_payload, token)
        else:
            entry["error"] = _error(payload, token)
        report["tested"].append(entry)
        if entry["ok"]:
            report["first_working_model"] = model
            report["ok"] = True
            break
    return report


def main() -> int:
    report = run_smoke()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
