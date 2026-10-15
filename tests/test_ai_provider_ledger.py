import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0005_ai_provider_ledger.sql"
WORKER = ROOT / "src" / "index.js"


class AIProviderLedgerTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(MIGRATION.read_text(encoding="utf-8"))

    def tearDown(self):
        self.db.close()

    def add_call(self, call_id, *, day="2026-10-15", provider="gemini",
                 role="diagnose", status="reserved", error_code=None):
        self.db.execute(
            "INSERT INTO ai_provider_calls "
            "(call_id, usage_day, provider, role, status, error_code) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (call_id, day, provider, role, status, error_code),
        )

    def test_migration_creates_durable_calls_and_provider_state_tables(self):
        tables = {
            row[0] for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertIn("ai_provider_calls", tables)
        self.assertIn("ai_provider_state", tables)

        columns = {
            row[1] for row in self.db.execute("PRAGMA table_info(ai_provider_calls)")
        }
        self.assertTrue({
            "call_id", "usage_day", "provider", "role", "run_id", "status",
            "error_code", "prompt_tokens", "output_tokens", "total_tokens",
            "cooldown_until", "finish_marker", "started_at", "finished_at",
        }.issubset(columns))

    def test_terminal_and_reserved_calls_all_count_toward_daily_limit(self):
        self.add_call("call-reserved-0001", status="reserved")
        self.add_call("call-success-0001", status="succeeded")
        self.add_call("call-failed-00001", status="failed", error_code="rate_limited")
        self.add_call("call-unknown-0001", status="unknown")
        self.add_call("call-next-day-00001", day="2026-10-16")
        self.add_call("call-next-provider-01", provider="future-provider")

        used = self.db.execute(
            "SELECT COUNT(*) FROM ai_provider_calls "
            "WHERE usage_day = ? AND provider = ?",
            ("2026-10-15", "gemini"),
        ).fetchone()[0]
        self.assertEqual(used, 4)

    def test_role_status_and_token_constraints_reject_invalid_rows(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_call("invalid-role-call-0001", role="publish")
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_call("invalid-status-call-001", status="retry_forever")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO ai_provider_calls "
                "(call_id, usage_day, provider, role, prompt_tokens) "
                "VALUES (?, ?, ?, ?, ?)",
                ("invalid-token-count-01", "2026-10-15", "gemini", "diagnose", -1),
            )

    def test_cooldown_state_only_accepts_known_throttling_reasons(self):
        self.db.execute(
            "INSERT INTO ai_provider_state (provider, cooldown_until, last_error_code) "
            "VALUES (?, ?, ?)",
            ("gemini", "2026-10-15T12:05:00.000Z", "rate_limited"),
        )
        self.assertEqual(
            self.db.execute("SELECT last_error_code FROM ai_provider_state WHERE provider = 'gemini'").fetchone()[0],
            "rate_limited",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO ai_provider_state (provider, cooldown_until, last_error_code) "
                "VALUES (?, ?, ?)",
                ("other-provider", "2026-10-15T12:05:00.000Z", "secret_leaked"),
            )

    def test_worker_has_single_active_gemini_provider_and_five_call_limit(self):
        source = WORKER.read_text(encoding="utf-8")
        self.assertIn("const CODE_AI_PROVIDER_LIMITS = Object.freeze({ gemini: 5 });", source)
        self.assertIn('"code_ai_provider_not_enabled"', source)
        self.assertIn("CODE_AI_PROVIDER_LIMITS[provider]", source)

    def test_reservation_is_atomic_and_rejects_active_cooldown(self):
        source = WORKER.read_text(encoding="utf-8")
        self.assertIn(
            "SELECT COUNT(*) FROM ai_provider_calls WHERE usage_day = ? AND provider = ?",
            source,
        )
        self.assertIn(
            "NOT EXISTS (SELECT 1 FROM ai_provider_state WHERE provider = ? AND cooldown_until > ?)",
            source,
        )
        self.assertIn("ON CONFLICT(call_id) DO NOTHING", source)
        self.assertIn("RETURNING call_id, usage_day, provider, role, status, started_at", source)

    def test_quota_routes_are_behind_existing_bearer_authorization(self):
        source = WORKER.read_text(encoding="utf-8")
        auth = source.index("if (!authorized(request, env))")
        status_route = source.index('if (path === "/api/brain/ai-provider/status")')
        reserve_route = source.index('if (path === "/api/brain/ai-provider/reserve")')
        finish_route = source.index('if (path === "/api/brain/ai-provider/finish")')
        self.assertLess(auth, status_route)
        self.assertLess(auth, reserve_route)
        self.assertLess(auth, finish_route)

    def test_finish_uses_single_use_state_transition_and_marker_gated_cooldown(self):
        source = WORKER.read_text(encoding="utf-8")
        self.assertIn("AND status = 'reserved'", source)
        self.assertIn("const finishMarker = crypto.randomUUID();", source)
        self.assertIn("AND finish_marker = ?)", source)
        self.assertIn("await env.DB.batch([updateCall, updateCooldown])", source)
        self.assertIn("code_ai_call_already_finished", source)

    def test_worker_fail_closed_when_ledger_database_is_unavailable(self):
        source = WORKER.read_text(encoding="utf-8")
        self.assertIn('"code_ai_ledger_unavailable"', source)
        self.assertIn('"code_ai_daily_budget_exhausted"', source)
        self.assertIn('"code_ai_provider_cooldown_active"', source)


if __name__ == "__main__":
    unittest.main()
