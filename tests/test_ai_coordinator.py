import unittest

from agent.brain.ai_coordinator import (
    AIRoleCoordinator,
    CoordinatorInputError,
    ProviderCallError,
    ProviderSpec,
    RoleStep,
)

ALL_ROLES = frozenset({"diagnose", "propose_patch", "review_patch"})


def make_provider(name, roles, call):
    return ProviderSpec(name=name, roles=frozenset(roles), call=call)


class AIRoleCoordinatorTests(unittest.TestCase):
    def test_ordered_roles_use_independent_patch_reviewer(self):
        calls = []

        def primary(role, request):
            calls.append(("primary", role, request))
            return {
                "diagnose": {"summary": "failure"},
                "propose_patch": {"patch": "proposal"},
            }[role]

        def backup(role, request):
            calls.append(("backup", role, request))
            return {"decision": "reviewed", "approved": False}

        coordinator = AIRoleCoordinator([
            make_provider("primary", ALL_ROLES, primary),
            make_provider("backup", ALL_ROLES, backup),
        ])
        report = coordinator.run_plan([
            RoleStep("diagnose", {"traceback": "safe synthetic failure"}),
            RoleStep("propose_patch", {"source": "fixture.py"}),
            RoleStep("review_patch", {"require_independent_review": True}),
        ], initial_context={"run_id": "offline-test"})

        self.assertTrue(report.ok)
        self.assertEqual(report.status, "completed_untrusted_outputs")
        self.assertEqual(report.calls_used, 3)
        self.assertEqual(report.provider_by_role, {
            "diagnose": "primary",
            "propose_patch": "primary",
            "review_patch": "backup",
        })
        self.assertTrue(report.manual_review)
        self.assertEqual(report.results["diagnose"]["summary"], "failure")
        self.assertIn("diagnose", calls[1][2]["previous_results"])
        self.assertIn("propose_patch", calls[2][2]["previous_results"])
        serialized = report.to_dict()
        self.assertFalse(serialized["mutations_applied"])
        self.assertFalse(serialized["publishing"])

    def test_rate_limit_falls_back_and_cools_down_provider(self):
        now = [1000.0]
        primary_calls = []
        backup_calls = []

        def limited(role, request):
            primary_calls.append(role)
            raise ProviderCallError("rate_limited", retry_after_seconds=60)

        def backup(role, request):
            backup_calls.append(role)
            return {"summary": "fallback result"}

        coordinator = AIRoleCoordinator(
            [
                make_provider("primary", {"diagnose"}, limited),
                make_provider("backup", {"diagnose"}, backup),
            ],
            cooldown_seconds=30,
            clock=lambda: now[0],
        )
        first = coordinator.run_plan([RoleStep("diagnose", {"error": "synthetic"})])
        self.assertTrue(first.ok)
        self.assertEqual(primary_calls, ["diagnose"])
        self.assertEqual(backup_calls, ["diagnose"])
        self.assertEqual(first.attempts[0].error_code, "rate_limited")

        second = coordinator.run_plan([RoleStep("diagnose", {"error": "synthetic"})])
        self.assertTrue(second.ok)
        self.assertEqual(primary_calls, ["diagnose"])
        self.assertEqual(backup_calls, ["diagnose", "diagnose"])
        self.assertEqual(second.attempts[0].outcome, "skipped_cooldown")
        self.assertEqual(second.calls_used, 1)

        now[0] += 61
        third = coordinator.run_plan([RoleStep("diagnose", {"error": "synthetic"})])
        self.assertTrue(third.ok)
        self.assertEqual(primary_calls, ["diagnose", "diagnose"])

    def test_global_call_budget_prevents_an_extra_provider_attempt(self):
        calls = []

        def limited(role, request):
            calls.append("primary")
            raise ProviderCallError("rate_limited")

        def backup(role, request):
            calls.append("backup")
            return {"summary": "should not be called"}

        coordinator = AIRoleCoordinator(
            [
                make_provider("primary", {"diagnose"}, limited),
                make_provider("backup", {"diagnose"}, backup),
            ],
            max_calls_per_run=1,
        )
        report = coordinator.run_plan([RoleStep("diagnose")])
        self.assertFalse(report.ok)
        self.assertEqual(report.status, "provider_call_budget_exhausted")
        self.assertEqual(report.calls_used, 1)
        self.assertEqual(calls, ["primary"])
        self.assertTrue(report.manual_review)

    def test_non_retryable_provider_error_stops_failover(self):
        calls = []

        def rejected(role, request):
            calls.append("primary")
            raise ProviderCallError("configuration_invalid", retryable=False)

        def backup(role, request):
            calls.append("backup")
            return {"summary": "not called"}

        coordinator = AIRoleCoordinator([
            make_provider("primary", {"diagnose"}, rejected),
            make_provider("backup", {"diagnose"}, backup),
        ])
        report = coordinator.run_plan([RoleStep("diagnose")])
        self.assertFalse(report.ok)
        self.assertEqual(report.status, "provider_rejected")
        self.assertEqual(calls, ["primary"])

    def test_unexpected_exception_does_not_leak_exception_text(self):
        secret_marker = "FAKE-SENSITIVE-DO-NOT-LOG"

        def broken(role, request):
            raise RuntimeError("adapter crashed " + secret_marker)

        def backup(role, request):
            return {"summary": "safe fallback"}

        coordinator = AIRoleCoordinator([
            make_provider("primary", {"diagnose"}, broken),
            make_provider("backup", {"diagnose"}, backup),
        ])
        report = coordinator.run_plan([RoleStep("diagnose")])
        rendered = str(report.to_dict())
        self.assertTrue(report.ok)
        self.assertIn("provider_internal_error", rendered)
        self.assertNotIn(secret_marker, rendered)

    def test_invalid_provider_output_is_rejected_before_fallback(self):
        calls = []

        def invalid(role, request):
            calls.append("primary")
            return None

        def backup(role, request):
            calls.append("backup")
            return "usable plain-text result"

        coordinator = AIRoleCoordinator([
            make_provider("primary", {"diagnose"}, invalid),
            make_provider("backup", {"diagnose"}, backup),
        ])
        report = coordinator.run_plan([RoleStep("diagnose")])
        self.assertTrue(report.ok)
        self.assertEqual(report.results["diagnose"], "usable plain-text result")
        self.assertEqual(calls, ["primary", "backup"])
        self.assertEqual(report.attempts[0].error_code, "provider_output_invalid")

    def test_missing_role_provider_fails_closed(self):
        coordinator = AIRoleCoordinator([
            make_provider("diagnostic-only", {"diagnose"}, lambda role, request: {"summary": "ok"}),
        ])
        report = coordinator.run_plan([RoleStep("propose_patch", {"source": "safe"})])
        self.assertFalse(report.ok)
        self.assertEqual(report.status, "no_provider_for_role")
        self.assertEqual(report.calls_used, 0)

    def test_review_cannot_reuse_the_patch_proposer_as_independent_reviewer(self):
        coordinator = AIRoleCoordinator([
            make_provider(
                "only-provider", ALL_ROLES,
                lambda role, request: {"result": role},
            ),
        ])
        report = coordinator.run_plan([
            RoleStep("propose_patch", {"source": "fixture.py"}),
            RoleStep("review_patch", {"patch": "untrusted"}),
        ])
        self.assertFalse(report.ok)
        self.assertEqual(report.status, "independent_reviewer_unavailable")
        self.assertEqual(report.provider_by_role, {"propose_patch": "only-provider"})
        self.assertTrue(report.manual_review)

    def test_invalid_plan_order_is_rejected_before_provider_calls(self):
        calls = []
        coordinator = AIRoleCoordinator([
            make_provider("primary", ALL_ROLES, lambda role, request: calls.append(role) or {"result": role}),
        ])
        with self.assertRaisesRegex(CoordinatorInputError, "plan_role_order_invalid"):
            coordinator.run_plan([
                RoleStep("review_patch"),
                RoleStep("propose_patch"),
            ])
        self.assertEqual(calls, [])

    def test_empty_non_object_context_is_not_silently_accepted(self):
        calls = []
        coordinator = AIRoleCoordinator([
            make_provider("primary", {"diagnose"}, lambda role, request: calls.append(role) or {"ok": True}),
        ])
        with self.assertRaisesRegex(CoordinatorInputError, "initial_context_invalid_or_too_large"):
            coordinator.run_plan([RoleStep("diagnose")], initial_context=[])
        self.assertEqual(calls, [])

    def test_oversized_context_is_rejected_before_provider_calls(self):
        calls = []
        coordinator = AIRoleCoordinator([
            make_provider("primary", {"diagnose"}, lambda role, request: calls.append(role) or {"ok": True}),
        ])
        with self.assertRaisesRegex(CoordinatorInputError, "initial_context_invalid_or_too_large"):
            coordinator.run_plan(
                [RoleStep("diagnose")],
                initial_context={"blob": "x" * (33 * 1024)},
            )
        self.assertEqual(calls, [])

    def test_non_json_role_payload_is_rejected_before_provider_calls(self):
        calls = []
        coordinator = AIRoleCoordinator([
            make_provider("primary", {"diagnose"}, lambda role, request: calls.append(role) or {"ok": True}),
        ])
        with self.assertRaisesRegex(CoordinatorInputError, "role_payload_invalid_or_too_large"):
            coordinator.run_plan([RoleStep("diagnose", {"bad": object()})])
        self.assertEqual(calls, [])

    def test_provider_names_must_be_unique(self):
        provider = make_provider("duplicate", {"diagnose"}, lambda role, request: {"ok": True})
        with self.assertRaisesRegex(CoordinatorInputError, "provider_names_must_be_unique"):
            AIRoleCoordinator([provider, provider])

    def test_provider_retries_are_bounded_by_per_role_attempt_limit(self):
        calls = []

        def failing(name):
            def invoke(role, request):
                calls.append(name)
                raise ProviderCallError("temporarily_unavailable")
            return invoke

        providers = [
            make_provider("one", {"diagnose"}, failing("one")),
            make_provider("two", {"diagnose"}, failing("two")),
            make_provider("three", {"diagnose"}, failing("three")),
        ]
        report = AIRoleCoordinator(
            providers, max_attempts_per_role=2, max_calls_per_run=6,
        ).run_plan([RoleStep("diagnose")])
        self.assertFalse(report.ok)
        self.assertEqual(report.status, "provider_attempts_exhausted")
        self.assertEqual(calls, ["one", "two"])
        self.assertEqual(report.calls_used, 2)


if __name__ == "__main__":
    unittest.main()
