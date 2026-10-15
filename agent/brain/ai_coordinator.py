"""Bounded, provider-agnostic AI role coordinator.

This module only coordinates injected provider adapters. It does not make network
requests, execute model output, edit files, create pull requests, or publish content.
Cooldowns and call budgets are process-local; durable cross-run accounting is a
separate concern and must not be inferred from this class.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

ROLE_ORDER = ("diagnose", "propose_patch", "review_patch")
_SAFE_CODE = re.compile(r"[a-z0-9_-]{1,80}")
MAX_PROVIDER_COUNT = 8
MAX_CALLS_PER_RUN = 12
MAX_ATTEMPTS_PER_ROLE = 4
MAX_INPUT_BYTES = 32 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_COOLDOWN_SECONDS = 24 * 60 * 60
_COOLDOWN_CODES = frozenset({"rate_limited", "quota_exhausted"})


class CoordinatorInputError(ValueError):
    """Caller-supplied plan or context is invalid."""


class ProviderCallError(RuntimeError):
    """A provider adapter error reduced to a safe machine-readable code."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = True,
        retry_after_seconds: int | None = None,
    ) -> None:
        safe_code = str(code or "").lower()
        if not _SAFE_CODE.fullmatch(safe_code):
            safe_code = "provider_error"
        if retry_after_seconds is not None and (
            type(retry_after_seconds) is not int
            or not 0 <= retry_after_seconds <= MAX_COOLDOWN_SECONDS
        ):
            retry_after_seconds = None
        super().__init__(safe_code)
        self.code = safe_code
        self.retryable = bool(retryable)
        self.retry_after_seconds = retry_after_seconds


ProviderCallable = Callable[[str, Mapping[str, Any]], Any]


@dataclass(frozen=True)
class ProviderSpec:
    """One injected adapter and the roles it is permitted to perform."""

    name: str
    roles: frozenset[str]
    call: ProviderCallable

    def __post_init__(self) -> None:
        name = str(self.name or "").strip().lower()
        roles = frozenset(self.roles)
        if not _SAFE_CODE.fullmatch(name):
            raise CoordinatorInputError("provider_name_invalid")
        if not roles or not roles.issubset(ROLE_ORDER):
            raise CoordinatorInputError("provider_roles_invalid")
        if not callable(self.call):
            raise CoordinatorInputError("provider_callable_required")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "roles", roles)


@dataclass(frozen=True)
class RoleStep:
    """A single requested role with JSON-compatible input data."""

    role: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in ROLE_ORDER:
            raise CoordinatorInputError("role_not_allowed")
        if not isinstance(self.payload, Mapping):
            raise CoordinatorInputError("role_payload_must_be_object")


@dataclass(frozen=True)
class ProviderAttempt:
    role: str
    provider: str
    outcome: str
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "provider": self.provider,
            "outcome": self.outcome,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class CoordinatorReport:
    ok: bool
    status: str
    results: Mapping[str, Any]
    provider_by_role: Mapping[str, str]
    attempts: tuple[ProviderAttempt, ...]
    calls_used: int
    manual_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "results": dict(self.results),
            "provider_by_role": dict(self.provider_by_role),
            "attempts": [item.to_dict() for item in self.attempts],
            "calls_used": self.calls_used,
            "manual_review": self.manual_review,
            "mutations_applied": False,
            "publishing": False,
        }


def _encode_json_object(value: Mapping[str, Any], *, max_bytes: int, error_code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CoordinatorInputError(error_code)
    payload = dict(value)
    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise CoordinatorInputError(error_code) from None
    if len(encoded) > max_bytes:
        raise CoordinatorInputError(error_code)
    return payload


def _valid_output(value: Any) -> bool:
    if isinstance(value, str):
        if not value.strip():
            return False
    elif type(value) is dict or type(value) is list:
        if not value:
            return False
    else:
        return False
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return False
    return len(encoded) <= MAX_OUTPUT_BYTES


class AIRoleCoordinator:
    """Run ordered AI roles with bounded failover and process-local cooldowns.

    Provider adapters receive the role and a request mapping. A role output is always
    treated as untrusted data. The review role must use a different provider from the
    successful patch-proposal role. No retries sleep: an exhausted provider list
    returns a safe report for the caller to schedule or present for human review.
    """

    def __init__(
        self,
        providers: Sequence[ProviderSpec],
        *,
        max_calls_per_run: int = 6,
        max_attempts_per_role: int = 2,
        cooldown_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        values = tuple(providers)
        if not values or len(values) > MAX_PROVIDER_COUNT:
            raise CoordinatorInputError("provider_count_out_of_range")
        names = [provider.name for provider in values]
        if len(set(names)) != len(names):
            raise CoordinatorInputError("provider_names_must_be_unique")
        if type(max_calls_per_run) is not int or not 1 <= max_calls_per_run <= MAX_CALLS_PER_RUN:
            raise CoordinatorInputError("call_budget_out_of_range")
        if type(max_attempts_per_role) is not int or not 1 <= max_attempts_per_role <= MAX_ATTEMPTS_PER_ROLE:
            raise CoordinatorInputError("role_attempt_budget_out_of_range")
        if type(cooldown_seconds) is not int or not 1 <= cooldown_seconds <= MAX_COOLDOWN_SECONDS:
            raise CoordinatorInputError("cooldown_out_of_range")
        if not callable(clock):
            raise CoordinatorInputError("clock_callable_required")

        self.providers = values
        self.max_calls_per_run = max_calls_per_run
        self.max_attempts_per_role = max_attempts_per_role
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._cooldown_until: dict[str, float] = {}

    def run_plan(
        self,
        plan: Sequence[RoleStep],
        *,
        initial_context: Mapping[str, Any] | None = None,
    ) -> CoordinatorReport:
        """Run a bounded role plan; stop at the first role with no safe result."""
        if not isinstance(plan, Sequence) or isinstance(plan, (str, bytes)) or not 1 <= len(plan) <= len(ROLE_ORDER):
            raise CoordinatorInputError("plan_length_out_of_range")
        steps = tuple(plan)
        if any(not isinstance(step, RoleStep) for step in steps):
            raise CoordinatorInputError("plan_step_invalid")
        indexes = [ROLE_ORDER.index(step.role) for step in steps]
        if indexes != sorted(indexes) or len(set(indexes)) != len(indexes):
            raise CoordinatorInputError("plan_role_order_invalid")
        if any(step.role == "review_patch" for step in steps) and "propose_patch" not in [step.role for step in steps]:
            raise CoordinatorInputError("review_requires_patch_proposal")

        context = _encode_json_object(
            ( {} if initial_context is None else initial_context), max_bytes=MAX_INPUT_BYTES,
            error_code="initial_context_invalid_or_too_large",
        )
        clean_steps = [
            RoleStep(step.role, _encode_json_object(
                step.payload, max_bytes=MAX_INPUT_BYTES,
                error_code="role_payload_invalid_or_too_large",
            ))
            for step in steps
        ]

        results: dict[str, Any] = {}
        providers_used: dict[str, str] = {}
        attempts: list[ProviderAttempt] = []
        calls_used = 0

        for step in clean_steps:
            request = {
                "initial_context": context,
                "input": dict(step.payload),
                "previous_results": dict(results),
            }
            ok, output, provider_name, status, calls_used = self._run_role(
                step.role,
                request,
                providers_used=providers_used,
                attempts=attempts,
                calls_used=calls_used,
            )
            if not ok:
                return CoordinatorReport(
                    ok=False,
                    status=status,
                    results=dict(results),
                    provider_by_role=dict(providers_used),
                    attempts=tuple(attempts),
                    calls_used=calls_used,
                    manual_review=True,
                )
            results[step.role] = output
            providers_used[step.role] = str(provider_name)

        # Even an independent model review is advisory: validation and owner approval
        # remain mandatory before any proposed patch may be applied or promoted.
        proposal_present = "propose_patch" in results
        return CoordinatorReport(
            ok=True,
            status="completed_untrusted_outputs" if proposal_present else "ok",
            results=dict(results),
            provider_by_role=dict(providers_used),
            attempts=tuple(attempts),
            calls_used=calls_used,
            manual_review=proposal_present,
        )

    def _run_role(
        self,
        role: str,
        request: Mapping[str, Any],
        *,
        providers_used: Mapping[str, str],
        attempts: list[ProviderAttempt],
        calls_used: int,
    ) -> tuple[bool, Any, str | None, str, int]:
        candidates = [provider for provider in self.providers if role in provider.roles]
        if not candidates:
            return False, None, None, "no_provider_for_role", calls_used

        if role == "review_patch":
            proposer = providers_used.get("propose_patch")
            candidates = [provider for provider in candidates if provider.name != proposer]
            if not candidates:
                return False, None, None, "independent_reviewer_unavailable", calls_used

        now = float(self._clock())
        active: list[ProviderSpec] = []
        for provider in candidates:
            if self._cooldown_until.get(provider.name, 0.0) > now:
                attempts.append(ProviderAttempt(
                    role=role, provider=provider.name,
                    outcome="skipped_cooldown", error_code="provider_cooldown_active",
                ))
            else:
                active.append(provider)
        if not active:
            return False, None, None, "provider_cooldown_active", calls_used

        for provider in active[: self.max_attempts_per_role]:
            if calls_used >= self.max_calls_per_run:
                return False, None, None, "provider_call_budget_exhausted", calls_used
            calls_used += 1
            try:
                output = provider.call(role, request)
            except ProviderCallError as exc:
                attempts.append(ProviderAttempt(
                    role=role, provider=provider.name,
                    outcome="failed", error_code=exc.code,
                ))
                if exc.code in _COOLDOWN_CODES:
                    delay = exc.retry_after_seconds or self.cooldown_seconds
                    self._cooldown_until[provider.name] = float(self._clock()) + delay
                if not exc.retryable:
                    return False, None, None, "provider_rejected", calls_used
                continue
            except Exception:
                # Never expose exception messages, request data, headers, or provider bodies.
                attempts.append(ProviderAttempt(
                    role=role, provider=provider.name,
                    outcome="failed", error_code="provider_internal_error",
                ))
                continue

            if not _valid_output(output):
                attempts.append(ProviderAttempt(
                    role=role, provider=provider.name,
                    outcome="rejected", error_code="provider_output_invalid",
                ))
                continue

            attempts.append(ProviderAttempt(
                role=role, provider=provider.name, outcome="succeeded",
            ))
            return True, output, provider.name, "ok", calls_used

        return False, None, None, "provider_attempts_exhausted", calls_used
