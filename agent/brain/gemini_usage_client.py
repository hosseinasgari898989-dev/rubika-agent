"""Authenticated, fail-closed client for the code-repair AI usage ledger.

This client talks only to the existing Rubika-Agent Worker D1 accounting API.
It does not call Cloudflare Workers AI or any model provider.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Mapping

ALLOWED_PROVIDERS = frozenset({"gemini", "groq", "openrouter", "workers_ai"})
ALLOWED_ROLES = frozenset({"diagnose", "propose_patch", "review_patch"})
ALLOWED_OUTCOMES = frozenset({"succeeded", "failed", "unknown"})
SAFE_CODE = re.compile(r"[a-z0-9_-]{1,80}")


class UsageLedgerError(RuntimeError):
    """Safe machine-readable ledger failure code."""

    def __init__(self, code: str):
        cleaned = str(code or "").lower()
        self.code = cleaned if SAFE_CODE.fullmatch(cleaned) else "usage_ledger_error"
        super().__init__(self.code)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward an Authorization header across Worker redirects."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class CodeAIUsageLedger:
    """Small stdlib-only HTTPS client for durable multi-provider reservation and usage."""

    def __init__(self, base_url: str, token: str, *, timeout_seconds: int = 8):
        parsed = urllib.parse.urlsplit((base_url or "").strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise UsageLedgerError("usage_ledger_url_invalid")
        clean_token = (token or "").strip()
        if len(clean_token) < 32:
            raise UsageLedgerError("usage_ledger_token_missing_or_short")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
            raise UsageLedgerError("usage_ledger_timeout_invalid")
        self.base_url = "https://" + parsed.netloc
        self.token = clean_token
        self.timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirect())

    @classmethod
    def from_env(cls) -> "CodeAIUsageLedger":
        base_url = os.getenv("AGENT_API_BASE_URL", "").strip()
        token = os.getenv("AGENT_API_TOKEN", "").strip()
        return cls(base_url, token)

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(
            dict(payload), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.token,
                "User-Agent": "rubika-agent-gemini-ledger/1.0",
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                if getattr(response, "status", 200) not in {200, 201}:
                    raise UsageLedgerError("usage_ledger_http_error")
                raw = response.read(16 * 1024).decode("utf-8")
        except urllib.error.HTTPError as exc:
            code = "usage_ledger_http_" + str(exc.code)
            try:
                error_body = json.loads(exc.read(4096).decode("utf-8", errors="replace"))
                candidate = error_body.get("error") if isinstance(error_body, dict) else None
                if isinstance(candidate, str) and SAFE_CODE.fullmatch(candidate):
                    code = candidate
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise UsageLedgerError(code) from None
        except UsageLedgerError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            raise UsageLedgerError("usage_ledger_unreachable") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise UsageLedgerError("usage_ledger_invalid_json") from None

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            raise UsageLedgerError("usage_ledger_invalid_json") from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            raw_code = result.get("error") if isinstance(result, dict) else None
            code = raw_code if isinstance(raw_code, str) and SAFE_CODE.fullmatch(raw_code) else "usage_ledger_rejected"
            raise UsageLedgerError(code)
        return result

    def reserve(self, *, provider: str, role: str, call_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        if provider not in ALLOWED_PROVIDERS:
            raise UsageLedgerError("code_ai_provider_not_enabled")
        if role not in ALLOWED_ROLES:
            raise UsageLedgerError("code_ai_role_invalid")
        identifier = call_id or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", identifier):
            raise UsageLedgerError("code_ai_call_id_invalid")
        if run_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", run_id):
            raise UsageLedgerError("code_ai_run_id_invalid")
        result = self._post(
            "/api/brain/ai-provider/reserve",
            {
                "provider": provider,
                "role": role,
                "call_id": identifier,
                **({"run_id": run_id} if run_id else {}),
            },
        )
        if result.get("reserved") is not True or result.get("call_id") != identifier:
            raise UsageLedgerError("usage_ledger_reservation_unconfirmed")
        return result

    def finish(
        self,
        *,
        provider: str,
        call_id: str,
        outcome: str,
        error_code: str | None = None,
        usage: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        if provider not in ALLOWED_PROVIDERS:
            raise UsageLedgerError("code_ai_provider_not_enabled")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", call_id):
            raise UsageLedgerError("code_ai_call_id_invalid")
        if outcome not in ALLOWED_OUTCOMES:
            raise UsageLedgerError("code_ai_outcome_invalid")

        clean_error = None
        if error_code is not None:
            clean_error = str(error_code).lower()
            if not SAFE_CODE.fullmatch(clean_error):
                raise UsageLedgerError("code_ai_error_code_invalid")

        token_map = {
            "prompt_tokens": "promptTokenCount",
            "output_tokens": "candidatesTokenCount",
            "total_tokens": "totalTokenCount",
        }
        payload: dict[str, Any] = {
            "provider": provider,
            "call_id": call_id,
            "outcome": outcome,
        }
        if clean_error:
            payload["error_code"] = clean_error
        for field, source in token_map.items():
            value = (usage or {}).get(source)
            if type(value) is int and 0 <= value <= 10_000_000:
                payload[field] = value
        if clean_error in {"rate_limited", "quota_exhausted"}:
            payload["retry_after_seconds"] = 300

        result = self._post("/api/brain/ai-provider/finish", payload)
        if result.get("call_id") != call_id or result.get("outcome") != outcome:
            raise UsageLedgerError("usage_ledger_finish_unconfirmed")
        return result
